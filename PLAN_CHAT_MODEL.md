# Chat Model Refactor — Implementation Plan (traceable)

> **Status:** Planned (no code written yet)
> **Date:** 2026-08-06
> **Scope:** Backend only (FastAPI + PostgreSQL/PostGIS + Redis + Alembic). Mobile is a follow-up.

---

## 1. Goal

Replace the current two-tier messaging system (`matches`-based chats + nullable `match_id`
"unmatched" threads) with a single **`chats`** table. A chat is created explicitly via a new
endpoint (`POST /chats`) that sends the **first message** in the same call and consumes the
daily chat limit. Likes (swipes) and matches remain fully independent of chats.

### Confirmed decisions
| # | Decision |
|---|----------|
| D1 | Keep `matches` table (mutual-like celebration). Chats are independent; messages never reference `matches`. |
| D2 | Chat button = **pure chat, no like**. No swipe record is created by `POST /chats`. |
| D3 | `chat.status` = `pending` \| `accepted`. While `pending`, the **initiator (starter) may send ≤ 2 messages total**; receiver acceptance flips status → `accepted` (unlimited both sides). |
| D4 | If the pair already has a **mutual like** (Swipe like both ways), a new chat is created `accepted` immediately. Otherwise `pending`. |
| D5 | Development stage, **no real data** → DB may be dropped/recreated; **no data migration** of old messages. |
| D6 | `initiator`/`recipient` are **semantic**, not sorted: `initiator_id` = who pressed chat (starter). One active chat per pair is enforced by a **partial unique index on `LEAST(initiator_id, recipient_id), GREATEST(initiator_id, recipient_id)`** (`WHERE is_active = true`), so order doesn't affect pair-uniqueness. |
| D7 | **Fully replace `/conversations`** with `/chats`. Old endpoint removed. |
| D8 | Migration files (`alembic/versions/`) added to `.gitignore` and untracked. |

---

## 2. New `Chat` model — `app/models/chat.py`

### Columns
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK, `uuid4` | |
| `initiator_id` | FK `users.id`, NOT NULL | who pressed "chat" (starter) |
| `recipient_id` | FK `users.id`, NOT NULL | |
| `status` | String, default `pending` | `pending` \| `accepted` |
| `last_message_id` | FK `messages.id`, **nullable** | denormalized "last message" pointer |
| `is_active` | Boolean, default `True` | soft archive/unmatch |
| `created_at` | DateTime tz, server default now() | |
| `updated_at` | DateTime tz, server default now(), onupdate now() | |

### Constraints / indexes
- **No** `CHECK (initiator_id < recipient_id)` — ordering removed so `initiator` can mean the actual starter.
- Partial **unique** index on the pair regardless of order:
  `Index("uq_chats_active_pair", text("LEAST(initiator_id, recipient_id)"), text("GREATEST(initiator_id, recipient_id)"), unique=True, postgresql_where=text("is_active = true"))`
- `Index("idx_chats_initiator_updated", "initiator_id", "updated_at")`
- `Index("idx_chats_recipient_updated", "recipient_id", "updated_at")`

### Relationships
- `initiator` → `User` (FK initiator_id)
- `recipient` → `User` (FK recipient_id)
- `messages` → `Message` (back_populates `chat`, cascade delete-orphan)
- `last_message` → `Message` (FK last_message_id, remote relationship)

---

## 3. `Message` model changes — `app/models/message.py`

### Column changes
- **Remove** `match_id` (was nullable FK → matches). Delete column + index.
- **Add** `chat_id` UUID **NOT NULL**, FK `chats.id`, `ondelete=CASCADE`.
- **Remove** `is_accepted` (moved to `Chat.status`). Delete column.
- Keep `sender_id`, `receiver_id`, `message_type`, `_content`/`content`, `reply_to_id`,
  media columns, delivery/read flags, delete flags, timestamps.
- Remove `match` relationship → add `chat` relationship (`back_populates="messages"`).

### Indexes (replace match-based set)
| Old | New |
|-----|-----|
| `idx_messages_match_sent (match_id, sent_at)` | `idx_messages_chat_sent (chat_id, sent_at)` |
| `idx_messages_receiver_delivered (receiver_id, is_delivered) WHERE is_delivered=false` | keep as-is |
| `idx_messages_receiver_read (receiver_id, is_read) WHERE is_read=false` | rename → `idx_messages_receiver_unread` |
| `idx_messages_match_recent (match_id, sent_at) WHERE is_deleted_for_all=false` | drop |

### Encryption pivot
- Content encryption key salt switches from `match_id` → `chat_id`.
- `Message.content` property: decrypt/encrypt using `str(self.chat_id)`.
- `get_encrypted_content()` / `get_decrypted_content_for_admin()` use `chat_id`.
- **Every message is now encrypted** (previously unmatched messages were plaintext).

---

## 4. New chats API — `app/api/v1/endpoints/chats.py` (prefix `/chats`)

### `POST /chats` — create-or-enter + send first message
Request body: `{ "user_id": UUID, "content": str }`
1. Validate: target user exists + active; not self; not blocked (either direction) → 403.
2. Normalize pair `(min_id, max_id)`.
3. Look up active chat via partial unique index.
   - **Exists** → return `200 {chat_id, is_new: false, status}` — **no message**, **no limit consumed**.
   - **Not exists** → continue to 4.
4. `RewardService.consume_chat(initiator)` → if `False` (non-premium, over daily limit) → `429` with same wording as today.
5. Determine `status`: `accepted` if mutual like exists (`Swipe` like both directions), else `pending` (D4).
6. Create `Chat(initiator_id, recipient_id, status)` + flush.
7. Create first `Message(chat_id, sender=initiator, receiver=recipient, content, message_type="text")`.
8. `chat.last_message_id = message.id`; commit atomically.
9. Push notification `NotificationService.notify_message(receiver, initiator_name, chat_id)`.
10. WebSocket publish `{type:"new_chat", chat_id, status}` + `{type:"new_message", chat_id, message}`.
11. Response `{chat_id, is_new: true, status, message: MessageResponse, chats_remaining_today}`.

Rate limit: `@limiter.limit("30/minute")` (matches discover/message limits).

### `POST /chats/{chat_id}/accept`
- Verify membership (initiator or recipient).
- If `pending` → set `status = accepted`, touch `updated_at`.
- Publish WS `{type:"chat_accepted", chat_id, status}`.
- Response `{chat_id, status, message: "Chat accepted"}`.

### `GET /chats` — conversation list (replaces `/conversations`)
- One query on `Chat` where `is_active=true` and (initiator_id = me OR recipient_id = me).
- Join the **other** user + profile + main approved photo; `selectinload` last_message.
- Filter out blocked pairs (either direction).
- For each chat: `other_user{id,name,age,main_photo_url,is_online,last_seen_at}`, `status`,
  `last_message{content,message_type,is_read,sent_at}`, `unread_count`, `updated_at`.
- Sort by `updated_at` desc. Pagination `limit` (1..50) / `offset`.
- Presence: bulk Redis online status for page users.

### `GET /chats/{chat_id}` — single chat detail
- Membership check.
- Returns other user, status, last_message, unread_count, created/updated.

### `GET /chats/{chat_id}/messages` — chat history (moved from `/messages`)
See section 6 — endpoints relocate to operate on `chat_id` directly (kept under `/chats`
or reused `/messages/{chat_id}` — decision in §12).

---

## 5. Real-time WebSocket changes

### `app/services/websocket_manager.py`
- `conversation_channel(...)` **simplified** → always `ws:chat:{chat_id}` (remove unmatched variant).
- `_typing_key(chat_id, user_id)` → `typing:{chat_id}:{user_id}`.
- `_chat_channel(match_id)` → rename to `_chat_channel(chat_id)`.
- Keep: `send_to_conversation`, presence, typing, personal `_user_channel` delivery,
  `broadcast_match` (matches still used for celebration).

### `app/api/v1/websocket/chat.py`
- `/ws/chat/{chat_id}` — identifier is now the **chat_id**.
- Verify chat exists, `is_active=true`, and `user_id` ∈ (initiator, recipient).
- Remove the `Match` lookup branch and the unmatched user-id branch entirely.
- Channel = `ws:chat:{chat_id}`.
- Events on the wire (both directions):
  - `user_online` / `user_offline` (existing)
  - `ping` / `pong`, `typing` / `typing_stopped` (existing, keyed by chat)
  - `read` / `messages_read` (existing)
  - `new_message` (on send — includes `chat_id`, message payload)
  - `new_chat` (on chat creation — delivered to recipient)
  - `chat_accepted` (on accept — delivered to both)

---

## 6. `messages.py` rewrite — `app/api/v1/endpoints/messages.py`

Endpoints now operate exclusively on `chat_id`. Remove the dual (match-or-user) resolution.

| Endpoint | Change |
|----------|--------|
| `GET /messages/{chat_id}` | chat history. Identifier = chat_id. Remove `get_match_or_chat`; single membership query. Keep cursor (`before`) + legacy (`offset/limit`) pagination. |
| `POST /messages/{chat_id}/text` | send text. Guard: `status==accepted` → unlimited; `status==pending` → only initiator, and initiator sent messages in this chat `< 2` (count `sender_id==initiator_id`, `chat_id==…`). Else 403. Per-chat rate limit key `msg_rate:{user_id}:{chat_id}`. |
| `POST /messages/{chat_id}/photo` | allowed only when `status==accepted`. Media saved under `chat/media/{chat_id}/{message_id}` (media_service). |
| `POST /messages/{chat_id}/voice` | allowed only when `status==accepted`. |
| `POST /messages/{chat_id}/accept` | **deprecated** → moved to `POST /chats/{chat_id}/accept` (D7). Keep as thin alias OR remove. |
| `POST /messages/delivered` | unchanged (operates by message_ids). |
| `POST /messages/read` | unchanged. |
| `DELETE /messages/{message_id}` | unchanged. |
| `POST /messages/{message_id}/forward` | `target_match_id` → `target_chat_id`; receiver = other party of target chat. |
| `GET /messages/{message_id}/status` | unchanged. |

### Send-flow (text) pseudo
```
1. load chat by chat_id; verify membership; 404 if missing/inactive.
2. enforce send guard (§ send rule above).
3. rate limit (Redis incr `msg_rate:{user}:{chat_id}` / 60s, >30 → 429).
4. create encrypted Message(chat_id, sender, receiver, content, type).
5. chat.last_message_id = msg.id; commit (same txn).
6. notify_message(receiver, sender_name, chat_id).
7. WS publish new_message to channel.
8. return SendMessageResponse(id, sent_at, requires_acceptance: status==pending && sender==initiator, chat_accepted: status==accepted, chats_remaining_today).
```

---

## 7. `chat_service.py` changes

| Current | Replacement |
|---------|-------------|
| `get_or_create_daily_limit` | keep (still used by limits). |
| `can_start_new_chat` | **remove** — logic in `POST /chats` + `RewardService.consume_chat`. |
| `check_unmatched_message_limit` | **remove** — replaced by `can_send_in_chat(chat, user)` (pending/initiator/≤2). |
| `accept_unmatched_chat` | **remove** — replaced by `accept_chat(session, chat)`. |
| `increment_new_chat_count` | **remove** — `consume_chat` handles counting. |
| `create_encrypted_message` | `match_id` param → `chat_id`; always encrypt. |
| `send_message_with_encryption` | `match_id` → `chat_id`; notify with chat_id. |
| `get_decrypted_message_for_client` | decrypt via `chat_id`. |
| `get_message_for_admin` | decrypt via `chat_id`. |
| `delete_message` | unchanged logic (content wipe), chat_id-based decrypt only when needed. |
| `forward_message` | `target_match_id` → `target_chat_id`; membership + receiver from chat. |
| (new) `find_active_chat(session, a, b)` | pair lookup via partial unique index. |
| (new) `create_chat_with_first_message(...)` | shared by endpoint; returns (chat, message, is_new). |
| (new) `can_send_in_chat(chat, user_id, session)` | returns (can, reason, status). |

---

## 8. `media_service.py` changes
- `save_photo(file_data, match_id, message_id)` → param rename `match_id` → `chat_id`.
  Storage key `chat/photos/{chat_id}/{message_id}.jpg`.
- `save_voice(...)` → same rename; `chat/voice/{chat_id}/{message_id}.mp3`.
- `delete_media(match_id, ...)` → `delete_media(chat_id, ...)`.

---

## 9. Unchanged (independent) modules
- `swipes.py` — still records swipes + creates `Match` on mutual like. **No chat creation.**
- `matches.py` + `schemas/match.py` — celebration list/detail, untouched.
- `schemas/swipe.py`, `schemas/discover.py` (`SwipeResponse`) — untouched.
- `notification_service.notify_match` / `broadcast_match` — untouched.
- `core/encryption.py` — API unchanged; **callers** pass `chat_id` instead of `match_id`.

---

## 10. Schema changes

### `app/schemas/conversation.py` → `app/schemas/chat.py`
Replace entirely:
```python
class ChatUserResponse(BaseModel):
    id: UUID
    name: str
    age: int
    main_photo_url: Optional[str] = None
    is_online: bool = False
    last_seen_at: Optional[datetime] = None

class ChatLastMessage(BaseModel):
    content: Optional[str] = None
    message_type: str = "text"
    is_read: bool = False
    sent_at: datetime

class ChatResponse(BaseModel):
    id: UUID
    status: str  # pending | accepted
    user: ChatUserResponse
    last_message: Optional[ChatLastMessage] = None
    unread_count: int = 0
    updated_at: Optional[datetime] = None

class ChatListResponse(BaseModel):
    chats: List[ChatResponse]
    total: int
    next_offset: Optional[int] = None
```
Delete `ConversationResponse`, `ConversationListResponse`, `kind`, `is_accepted` from list schema.

### `app/schemas/message.py`
- `MessageResponse.match_id` → `chat_id: UUID`.
- Remove `is_accepted` field.
- `ForwardMessageRequest.target_match_id` → `target_chat_id: UUID`.
- Add `StartChatRequest(BaseModel)`: `user_id: UUID`, `content: str` (min 1, max 5000).
- Add `StartChatResponse(BaseModel)`:
  `chat_id`, `is_new: bool`, `status: str`, `message: Optional[MessageResponse]`, `chats_remaining_today: Optional[int]`.
- Add `AcceptChatResponse`: `chat_id: UUID`, `status: str`, `message: str`.

---

## 11. Routing / wiring (`app/main.py`, `app/api/v1/endpoints/__init__.py`)
- Include new `chats` router.
- **Remove** `conversations` router import/include (D7).
- `messages` router stays under `/messages`.
- Ensure `Chat` and updated `Message` models are imported so Alembic sees them.

---

## 12. Open implementation detail (decide before coding)
- Chat history endpoint home: keep `GET /messages/{chat_id}` (symmetric with send/photo/voice),
  OR expose as `GET /chats/{chat_id}/messages`. **Recommend:** keep `/messages/{chat_id}`
  so the existing mobile `getChatHistory` path shape is preserved; everything else moves to `/chats`.

---

## 13. Alembic migration
- New migration (autogenerate after model edits) that:
  1. creates `chats`
  2. drops `messages.match_id` + `messages.is_accepted` + match-based indexes
  3. adds `messages.chat_id` NOT NULL FK + chat indexes
- Because D5 (no real data), migration can drop/recreate or truncate `messages` as needed.
- **NOTE:** `alembic/versions/` is now gitignored (D8). Each dev regenerates locally.
- Pre-existing heads: `16268284c9f0` chain exists in git history but is no longer tracked;
  a clean local autogenerate should be applied against an empty/dropped DB.

---

## 14. Test plan (updated)

> Policy: never run the full suite. Start test docker, run only affected files, stop docker.

### `tests/done/test_messages.py` — rewrite
- `test_send_text_message_in_matched_chat` → build chat via `POST /chats` (mutual-like pair → `accepted`), send text, assert 200 + `chat_accepted == true`.
- `test_get_chat_history` → create chat + send, `GET /messages/{chat_id}`.
- `test_unmatched_chat_limit` → **replaced** by pending rule:
  - create chat (no mutual like → `pending`), initiator sends 2 messages OK, 3rd → 403.
- `test_accept_chat` → initiator sends 2; recipient `POST /chats/{chat_id}/accept` → 200, status accepted; initiator 3rd message → 200.
- `test_delete_message_for_me` → matched-chat (accepted) → delete → 200.
- `test_mark_messages_as_read` → accepted chat → read → status `is_read == true`.
- Photo/voice tests → all use accepted chats; unmatched-fail tests now target `pending` chat (photo/voice → 403).

### `tests/done/test_conversations.py` → rewrite against `/chats`
- List after creating chat → one entry, other-user fields, last_message, status.
- No duplicate conversation for same pair.
- Pending vs accepted status surfaced.
- Blocked pair excluded from list.
- Pagination `limit/offset` + `next_offset`.

### `tests/done/test_matches.py` — mostly unchanged
- Verify matches still created on mutual like (unchanged). Confirm matches list does NOT include chat data.

### `tests/done/test_swipes.py` — unchanged
- Swipes independent; verify swipe alone does not create a chat.

### `tests/done/test_websocket.py` — update
- Connect to `/ws/chat/{chat_id}`; receive `new_message`, `chat_accepted`, typing, read, presence.
- Remove unmatched-channel tests.

### New test file `tests/done/test_chats.py`
- `POST /chats` new → `is_new=true`, `status=pending` (no mutual like), message content present, `chats_remaining_today` decremented for free user.
- `POST /chats` existing → `is_new=false`, `status` returned, **no new message row**, no limit decrement.
- Auto-accept: mutual like first → chat `status=accepted`.
- Blocked target → 403.
- Self-chat → 400.
- Daily limit exhaustion → 429.
- `POST /chats/{id}/accept` membership check (non-member → 404/403).
- `GET /chats/{id}` detail + membership.
- Encryption: message content decrypted via `chat_id` key.

### Fixtures affected
- `reset_state` truncates tables — add `chats` to the truncation list (verify it currently
  truncates `matches`/`messages`; include new table).

---

## 15. Mobile follow-up (NOT part of this change — future commit)
- `Message.matchId` → `chatId`; add `Chat` model (`status`, `user`, `lastMessage`, `unreadCount`).
- `ChatService`: `/chats` list, `POST /chats`, accept; remove `/conversations`.
- Discover/Search: **separate** Like button (swipe) and Chat button (`POST /chats` + redirect to chat screen).
- Chat screen opened via `chat_id`; notification-profile screen uses `/chats`.

---

## 16. Implementation order (when coding)
1. Models: `chat.py` new; `message.py` edit; `__init__`/imports.
2. `core/encryption.py` callers → chat_id (no signature change).
3. `schemas/chat.py` (new), `schemas/message.py` (edit).
4. `chat_service.py` (rewrite helpers), `media_service.py` (param rename).
5. `chats.py` endpoint (new), `messages.py` (rewrite), `conversations.py` (delete).
6. `websocket_manager.py` + `api/v1/websocket/chat.py`.
7. `main.py` wiring.
8. Alembic migration (regenerate locally; gitignored).
9. Tests: update + new `test_chats.py`.
10. Run affected tests via docker; `flutter analyze lib` untouched (no mobile yet).

---

## 17. Verification checklist
- [ ] `POST /chats` creates pending chat + first message + consumes limit (free user).
- [ ] Repeated `POST /chats` returns existing chat, no duplicate message.
- [ ] Pending: initiator ≤2 messages; 3rd → 403. Recipient accept → unlimited.
- [ ] Mutual-like pair → auto-accepted chat.
- [ ] Blocked/self/inactive guard.
- [ ] `/chats` list correct status, last message, unread, online, blocked filter.
- [ ] WS: new_message / new_chat / chat_accepted / typing / read / presence on chat_id channel.
- [ ] All message content encrypted under chat_id key (no plaintext rows).
- [ ] `/conversations` removed; no lingering `match_id` in message layer.
- [ ] Affected tests pass (test_chats, test_messages, test_conversations, test_websocket, test_matches, test_swipes).
