# Messaging & WebSocket Guide

## Iranian Dating App — Backend + Flutter

> Complete reference for the chat messaging system.
> Covers all HTTP REST endpoints and WebSocket events.
> Written for Flutter mobile developers integrating with the Bondi backend.

---

## 1. Overview

The messaging system uses **two transport layers** that work together:

| Layer | Transport | Purpose |
|-------|-----------|---------|
| **HTTP REST** | HTTPS POST/GET/DELETE | Persist messages to DB, enforce auth, rate limits, encryption |
| **WebSocket** | WS (wss:// in prod) | Real-time push delivery to the recipient, typing indicators, presence |

### Why both are needed

- **HTTP POST** handles the heavy lifting: validation, encryption, DB storage, rate limiting, push notifications. It is the authoritative write operation.
- **WebSocket** is the real-time notification pipe: it pushes new messages to the recipient instantly without polling. It also streams typing indicators, online/offline status, and read receipts.

If you only used HTTP, the recipient would need to poll `GET /messages/{id}` every few seconds — wasting battery and bandwidth. If you only used WebSocket, messages would be lost when connections drop (no persistent storage).

---

## 2. Architecture Diagram

```
Flutter App                    Backend                         Redis / DB
───────                       ───────                         ──────────
   │                              │
   │  POST /messages/{id}/text   │
   │  (send text message) ─────► │
   │                              │  ├─ Validates, encrypts, stores in PostgreSQL
   │  ◄── 200 SendMessageResponse│  └─ Enqueues BackgroundTask for WebSocket push
   │                              │
   │                              │  ── BackgroundTask ──────────────────┐
   │                              │  publish to Redis Pub/Sub           │
   │                              │  channel: ws:chat:{match_id} ──────►│
   │                              │  ┌──────────────────────────────────┐│
   │                              │  │  WebSocket worker (any process) ││
   │                              │  │  delivers to recipient's socket ││
   │                              │  └──────────────────────────────────┘│
   │                              │
   │  WS /ws/chat/{matchId} ◄────│  (persistent connection)
   │  receives real-time events  │
   │  ├─ new_message             │
   │  ├─ typing                  │
   │  ├─ typing_stopped          │
   │  ├─ messages_read           │
   │  ├─ user_online             │
   │  ├─ user_offline            │
   │  └─ pong (to ping)          │
```

---

## 3. HTTP API Endpoints

All message endpoints are prefixed with `/api/v1` and require authentication (`Authorization: Bearer <JWT>`).

### 3.1 Send Text Message

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/v1/messages/{identifier}/text` |
| **Content-Type** | `application/json` |
| **Rate Limit** | 60/min (plus 30/min per-match Redis limit) |

**Request Body** (`TextMessageRequest`):

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `content` | `string` | Yes | 1–5000 characters |
| `reply_to_id` | `UUID` | No | ID of a message to reply to |

**Example Request:**
```json
{
  "content": "Hey! How are you?",
  "reply_to_id": null
}
```

**Response** (`SendMessageResponse`) — 200:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `UUID` | The new message ID |
| `sent_at` | `ISO datetime` | When the message was sent |
| `requires_acceptance` | `bool` | True if unmatched chat, not yet accepted |
| `chat_accepted` | `bool` | True if chat is active (matched or accepted) |
| `chats_remaining_today` | `int \| null` | Remaining new chats for free users |

**Example Response:**
```json
{
  "id": "a1b2c3d4-...",
  "sent_at": "2026-07-31T12:00:00Z",
  "requires_acceptance": false,
  "chat_accepted": true,
  "chats_remaining_today": null
}
```

---

### 3.2 Send Photo Message

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/v1/messages/{identifier}/photo` |
| **Content-Type** | `multipart/form-data` |
| **Rate Limit** | 30/min |

**Request Body** (multipart form):

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `file` | `binary` (image) | Yes | JPEG, PNG, WEBP, JPG; ≤ `MAX_CHAT_PHOTO_SIZE_MB` |
| `caption` | `string` | No | Max 500 characters |

**Example (Flutter):**
```dart
var request = http.MultipartRequest(
  'POST',
  Uri.parse('https://api.bondi.ir/api/v1/messages/$matchId/photo'),
);
request.fields['caption'] = 'Look at this!';
request.files.add(await http.MultipartFile.fromPath('file', imagePath));
```

**Response** (`SendMessageResponse`) — 200 (same shape as text, `requires_acceptance` is always `false`, `chat_accepted` is always `true`)

---

### 3.3 Send Voice Message

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/v1/messages/{identifier}/voice` |
| **Content-Type** | `multipart/form-data` |
| **Rate Limit** | 30/min |

**Request Body** (multipart form):

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `file` | `binary` (audio) | Yes | MP3, ≤ `MAX_CHAT_VOICE_SIZE_MB`, duration ≤ `MAX_CHAT_VOICE_DURATION` (120s) |
| `duration` | `integer` | Yes | Voice duration in seconds (1–120) |

**Example:**
```dart
request.fields['duration'] = '45';
request.files.add(await http.MultipartFile.fromPath('file', voicePath));
```

**Response** (`SendMessageResponse`) — 200 (same as photo)

---

### 3.4 Get Chat History

| Field | Value |
|-------|-------|
| **Method** | `GET` |
| **Path** | `/api/v1/messages/{identifier}` |
| **Auth** | Required |
| **Rate Limit** | 60/min |

**Path Parameter:**

| Param | Type | Description |
|-------|------|-------------|
| `identifier` | `UUID` | Either a **match_id** (for matched chats) or a **user_id** (for unmatched chats) |

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | `int` | 30 | Messages per page (1–50) |
| `offset` | `int` | 0 | Legacy offset pagination |
| `before` | `ISO datetime` | — | Cursor pagination: fetch messages older than this timestamp |

**Cursor Pagination (Recommended):**
- Load the first page with no `before` parameter
- Take the `sent_at` of the oldest message in the response
- Pass it as `before` for the next page

**Response** (`MessageListResponse`):

| Field | Type | Description |
|-------|------|-------------|
| `messages` | `list` | Array of `MessageResponse` objects (oldest first) |
| `total` | `int` | Total message count in this chat |
| `next_offset` | `int \| null` | Offset for next page (null if no more) |

**`MessageResponse` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | `UUID` | Message ID |
| `match_id` | `UUID \| null` | Match ID (null for unmatched chats) |
| `sender_id` | `UUID` | Who sent this message |
| `receiver_id` | `UUID` | Who received this message |
| `message_type` | `str` | `"text"`, `"photo"`, or `"voice"` |
| `content` | `str \| null` | Decrypted text content (null for photo/voice) |
| `media_url` | `str \| null` | Signed URL for photo/voice media |
| `media_duration` | `int \| null` | Duration in seconds (voice only) |
| `reply_to` | `object \| null` | `ReplyToResponse` if this is a reply |
| `is_sent` | `bool` | Message was sent by the requesting user |
| `is_delivered` | `bool` | Received by the other user |
| `is_read` | `bool` | The other user has read it |
| `is_accepted` | `bool` | Chat acceptance status (unmatched chats) |
| `sent_at` | `ISO datetime` | |
| `delivered_at` | `ISO datetime \| null` | |
| `read_at` | `ISO datetime \| null` | |

---

### 3.5 Mark Messages as Delivered

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/v1/messages/delivered` |
| **Rate Limit** | 100/min |

**Request Body** (`MarkReadRequest`):

| Field | Type | Required |
|-------|------|----------|
| `message_ids` | `list[UUID]` | Yes |

**Response:** `{"message": "N messages marked as delivered"}`

---

### 3.6 Mark Messages as Read

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/v1/messages/read` |
| **Rate Limit** | 100/min |

**Request Body:** Same as delivered — `{"message_ids": [uuid1, uuid2, ...]}`

**Response:** `{"message": "N messages marked as read"}`

---

### 3.7 Accept Unmatched Chat

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/v1/messages/{identifier}/accept` |
| **Rate Limit** | 20/min |

**Description:** Converts an unmatched chat to an accepted (unlimited) chat.

**Response** (`AcceptChatResponse`):
```json
{
  "message": "Chat accepted. You can now send unlimited messages.",
  "is_accepted": true
}
```

---

### 3.8 Delete a Message

| Field | Value |
|-------|-------|
| **Method** | `DELETE` |
| **Path** | `/api/v1/messages/{message_id}` |
| **Rate Limit** | 30/min |

**Query Parameter:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `delete_for` | `string` | `"me"` | `"me"` = delete for self, `"everyone"` = delete for both (sender only, <1 hour old) |

**Response:** `{"message": "Message deleted for me"}`

---

### 3.9 Forward a Message

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/v1/messages/{message_id}/forward` |
| **Rate Limit** | 30/min |

**Request Body** (`ForwardMessageRequest`):

| Field | Type | Required |
|-------|------|----------|
| `target_match_id` | `UUID` | Yes — the match to forward to |

**Response** (`ForwardMessageResponse`):
```json
{
  "message": "Message forwarded",
  "new_message_id": "uuid-of-new-message"
}
```

---

### 3.10 Get Message Status

| Field | Value |
|-------|-------|
| **Method** | `GET` |
| **Path** | `/api/v1/messages/{message_id}/status` |
| **Rate Limit** | 60/min |

**Response** (`MessageStatusResponse`):

| Field | Type | Description |
|-------|------|-------------|
| `id` | `UUID` | |
| `sent_at` | `ISO datetime` | |
| `delivered_at` | `ISO datetime \| null` | |
| `read_at` | `ISO datetime \| null` | |
| `is_delivered` | `bool` | |
| `is_read` | `bool` | |

---

## 4. WebSocket Connection

### 4.1 URL and Auth

| Property | Value |
|----------|-------|
| **Path** | `/ws/chat/{match_id}` (no `/api/v1` prefix) |
| **Protocol** | `ws://` (dev) or `wss://` (production) |
| **Auth** | JWT via query parameter `?token=<JWT>` |
| **Example** | `wss://api.bondi.ir/ws/chat/a1b2c3d4-...?token=<JWT>` |

### 4.2 Connection Flow

```
1. Client connects: ws://host/ws/chat/{match_id}?token={jwt}
2. Server decodes JWT, extracts user_id from "sub" claim
   → If invalid/expired: close code 4001, reason "Unauthorized"
3. Server checks match exists (is_active=true), user is a participant
   → If not found or not a participant: close code 4003, reason "Access denied"
4. Server accepts the WebSocket connection
5. Server:
   - Adds user to chat channel (Redis Pub/Sub: ws:chat:{match_id})
   - Sets online status: Redis key online:{user_id}, TTL 60s
   - Starts listener task for this match's channel
   - Broadcasts user_online event to the other participant
```

### 4.3 Disconnect / Timeout

- If no message is received for **40 seconds**, the server closes the connection.
- On disconnect, the server broadcasts `user_offline` and clears typing status.

---

## 5. Client → Server WS Messages

All messages are sent as JSON text frames over the WebSocket.

### 5.1 Ping / Keepalive

```json
{"type": "ping"}
```

The server responds with `{"type": "pong"}`. Use this to keep the connection alive and refresh online presence (60s TTL). Send every 30 seconds.

### 5.2 Typing Started

Send when the user begins typing:

```json
{"type": "typing"}
```

### 5.3 Typing Stopped

Send when the user stops typing or sends a message:

```json
{"type": "typing_stopped"}
```

### 5.4 Read Receipts

Send when the user reads messages:

```json
{
  "type": "read",
  "message_ids": ["uuid1", "uuid2", "uuid3"]
}
```

> **Note:** The WebSocket `read` event notifies the other user in real-time. The database update (`is_read=True`, `read_at=now()`) is done separately via the HTTP endpoint `POST /api/v1/messages/read`.

---

## 6. Server → Client WS Messages

All events are JSON objects with a top-level `type` field and a `data` payload.

### 6.1 New Message (`new_message`)

Sent to both participants when a message is sent by either user.

**Text message:**
```json
{
  "type": "new_message",
  "data": {
    "id": "a1b2c3d4-...",
    "message_type": "text",
    "content": "Hey! How are you?",
    "sender_id": "user-uuid",
    "sent_at": "2026-07-31T12:00:00Z"
  }
}
```

**Photo message:**
```json
{
  "type": "new_message",
  "data": {
    "id": "a1b2c3d4-...",
    "message_type": "photo",
    "media_url": "https://minio-host/photos-private/...?sig=...",
    "caption": "Look at this!",
    "sender_id": "user-uuid",
    "sent_at": "2026-07-31T12:00:00Z"
  }
}
```

**Voice message:**
```json
{
  "type": "new_message",
  "data": {
    "id": "a1b2c3d4-...",
    "message_type": "voice",
    "media_url": "https://minio-host/photos-private/...?sig=...",
    "duration": 45,
    "sender_id": "user-uuid",
    "sent_at": "2026-07-31T12:00:00Z"
  }
}
```

| Field | Text | Photo | Voice |
|-------|------|-------|-------|
| `content` | ✅ decrypted text | ✅ caption | ❌ absent |
| `media_url` | ❌ absent | ✅ signed URL | ✅ signed URL |
| `duration` | ❌ absent | ❌ absent | ✅ seconds |

> **Important:** `media_url` is a signed URL with a 15-minute expiry. Display the photo/voice immediately — do not cache the URL long-term.

### 6.2 Match Notification (`new_match`)

Sent when a mutual like creates a new match.

```json
{
  "type": "new_match",
  "data": {
    "match_id": "match-uuid",
    "user": {
      "id": "other-user-uuid",
      "name": "Sara",
      "age": 28,
      "main_photo_url": "https://minio-host/photos-public/...",
      "_note": "main_photo_url may be null if user has no main photo"
    }
  }
}
```

### 6.3 User Online (`user_online`)

Sent by the WS handler when a participant connects to the chat.

```json
{
  "type": "user_online",
  "user_id": "other-user-uuid"
}
```

### 6.4 User Offline (`user_offline`)

Sent by the WS handler when a participant disconnects.

```json
{
  "type": "user_offline",
  "user_id": "other-user-uuid"
}
```

### 6.5 Typing Indicator (`typing`)

Sent when the other user starts typing (published via WebSocketManager `set_typing()`, 5s TTL).

```json
{
  "type": "typing",
  "match_id": "match-uuid",
  "user_id": "other-user-uuid"
}
```

### 6.6 Typing Stopped (`typing_stopped`)

Sent when the other user stops typing, sends a message, or disconnects.

```json
{
  "type": "typing_stopped",
  "match_id": "match-uuid",
  "user_id": "other-user-uuid"
}
```

### 6.7 Read Receipts (`messages_read`)

Sent when the other user marks messages as read (via WS `read` command).

```json
{
  "type": "messages_read",
  "message_ids": ["uuid1", "uuid2"],
  "reader_id": "other-user-uuid"
}
```

### 6.8 Pong Response (`pong`)

Server responds to client's `ping`.

```json
{
  "type": "pong"
}
```

---

## 7. Message Envelope Format

Every WebSocket message from the server follows this structure:

```json
{
  "type": "<event_type>",
  "data": { ... }
}
```

- `type` — string, one of: `new_message`, `new_match`, `user_online`, `user_offline`, `typing`, `typing_stopped`, `messages_read`, `pong`
- `data` — object, varies by event type (see Section 6)

The `_target_user` field used internally by `WebSocketManager.send_to_match()` is **stripped** before delivery to the client. You will never see it in a received message.

---

## 8. Rate Limits

| Endpoint | Method | Limit |
|----------|--------|-------|
| `GET /messages/{identifier}` | GET | 60/min |
| `POST /messages/{identifier}/text` | POST | 60/min |
| `POST /messages/{identifier}/photo` | POST | 30/min |
| `POST /messages/{identifier}/voice` | POST | 30/min |
| `POST /messages/{identifier}/accept` | POST | 20/min |
| `POST /messages/delivered` | POST | 100/min |
| `POST /messages/read` | POST | 100/min |
| `DELETE /messages/{message_id}` | DELETE | 30/min |
| `POST /messages/{message_id}/forward` | POST | 30/min |
| `GET /messages/{message_id}/status` | GET | 60/min |

Additionally, `POST /messages/{identifier}/text` enforces a **per-match in-memory rate limit**: max **30 messages per minute per sender per chat**.

---

## 9. Real-Time Flow Examples

### 9.1 Sending a Text Message

```
Flutter App                    Backend                        WebSocket
───────────                    ───────                        ──────────
1. User taps "Send"
2. POST /messages/{id}/text ──►│
   {content: "Hello"}          │  ├─ Validates, encrypts, stores in DB
   ◄── 200 {id, sent_at}       │  ├─ Enqueues background WS push
                               │  │
                               │  └─ BackgroundTask ──► publish to ws:chat:{match_id}
                               │                                  │
3. Show message in UI   ◄──────┘                                  │
4. (simultaneously)       ◄────────────────────────────────────────┘
                           ws message: {"type":"new_message", "data":{...}}
```

### 9.2 Typing Indicator

```
Flutter A                       Flutter B
───────────                     ──────────
1. User starts typing
2. WS send: {"type":"typing"} ──►│
                                  │  └─ publish typing event to ws:chat:{match_id}
                                  │                                  │
3. User sees "typing..." ◄───────┘
4. User sends message
5. WS send: {"type":"typing_stopped"} ──►│
                                        │  └─ publish typing_stopped event
6. "typing..." disappears ◄─────────────┘
```

### 9.3 Read Receipts

```
Flutter B                       Flutter A
───────────                     ──────────
1. User opens chat
2. Post /messages/read ──────►│  (DB: marks messages is_read=true)
   {message_ids: [uuid1, uuid2]} │
   ◄── 200                      │
3. (simultaneously)             │
                                │  └─ WS publish messages_read event
4. Show "Read" checkmark ◄─────┘
   ws message: {"type":"messages_read", "message_ids":[...], "reader_id":"B"}
```

---

## 10. Flutter Implementation Guide

### 10.1 WebSocket Connection Helper

```dart
class ChatWebSocketService {
  RawWebSocket? _socket;
  final String matchId;
  final String jwtToken;
  final _messageController = StreamController<Map<String, dynamic>>.broadcast();
  final _connectionStateController = StreamController<bool>.broadcast();

  ChatWebSocketService({required this.matchId, required this.jwtToken});

  Stream<Map<String, dynamic>> get messages => _messageController.stream;
  Stream<bool> get connectionState => _connectionStateController.stream;

  static const _heartbeatInterval = Duration(seconds: 30);
  static const _reconnectMaxAttempts = 6;
  static const _baseReconnectDelay = Duration(seconds: 1);

  int _retryCount = 0;
  Timer? _heartbeatTimer;
  Timer? _reconnectTimer;

  Future<void> connect() async {
    final uri = Uri.parse(
      'wss://api.bondi.ir/ws/chat/$matchId?token=$jwtToken',
    );

    try {
      _socket = await RawWebSocket.connect(uri);
      _retryCount = 0;
      _connectionStateController.add(true);

      // Start heartbeat
      _heartbeatTimer?.cancel();
      _heartbeatTimer = Timer.periodic(_heartbeatInterval, (_) {
        _send({'type': 'ping'});
      });

      // Listen for messages
      _socket!.listen(
        (data) {
          try {
            final parsed = json.decode(data as String) as Map<String, dynamic>;
            _handleServerMessage(parsed);
          } catch (_) {
            // Ignore malformed JSON
          }
        },
        onDone: _onDisconnected,
        onError: (_) => _onDisconnected(),
      );
    } catch (e) {
      _onDisconnected();
    }
  }

  void _handleServerMessage(Map<String, dynamic> msg) {
    final type = msg['type'] as String?;

    switch (type) {
      case 'pong':
        // Keepalive response, no action needed
        break;
      default:
        _messageController.add(msg);
        break;
    }
  }

  void _onDisconnected() {
    _heartbeatTimer?.cancel();
    _connectionStateController.add(false);
    _attemptReconnect();
  }

  void _attemptReconnect() {
    if (_retryCount >= _reconnectMaxAttempts) return;

    final delay = Duration(seconds: (_baseReconnectDelay.inSeconds * (1 << _retryCount)));
    // Cap at 30 seconds
    final cappedDelay = delay > const Duration(seconds: 30)
        ? const Duration(seconds: 30)
        : delay;

    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(cappedDelay, () {
      _retryCount++;
      connect();
    });
  }

  void _send(Map<String, dynamic> data) {
    _socket?.add(json.encode(data));
  }

  void sendPing() => _send({'type': 'ping'});

  void sendTyping() => _send({'type': 'typing'});

  void sendTypingStopped() => _send({'type': 'typing_stopped'});

  void sendReadReceipt(List<Uuid> messageIds) {
    _send({
      'type': 'read',
      'message_ids': messageIds.map((id) => id.toString()).toList(),
    });
  }

  Future<void> dispose() async {
    _heartbeatTimer?.cancel();
    _reconnectTimer?.cancel();
    await _socket?.close();
    await _messageController.close();
    await _connectionStateController.close();
  }
}
```

### 10.2 Typing Detection with Debounce

```dart
class TypingIndicatorController {
  Timer? _typingTimer;
  bool _isTyping = false;

  void onTextChanged(ChatWebSocketService ws, String text) {
    if (!_isTyping) {
      _isTyping = true;
      ws.sendTyping();
    }

    _typingTimer?.cancel();
    _typingTimer = Timer(const Duration(seconds: 3), () {
      _isTyping = false;
      ws.sendTypingStopped();
      _typingTimer = null;
    });
  }

  void onMessageSent(ChatWebSocketService ws) {
    _typingTimer?.cancel();
    _typingTimer = null;
    _isTyping = false;
    ws.sendTypingStopped();
  }

  void dispose() {
    _typingTimer?.cancel();
  }
}
```

### 10.3 Send a Text Message (HTTP POST)

```dart
Future<SendMessageResponse> sendTextMessage(
  String matchId,
  String content,
  String authToken,
) async {
  final response = await http.post(
    Uri.parse('https://api.bondi.ir/api/v1/messages/$matchId/text'),
    headers: {
      'Authorization': 'Bearer $authToken',
      'Content-Type': 'application/json',
    },
    body: json.encode({'content': content}),
  );

  if (response.statusCode == 200) {
    return SendMessageResponse.fromJson(json.decode(response.body));
  }
  throw Exception('Failed to send message: ${response.statusCode}');
}
```

### 10.4 Send a Photo Message (HTTP POST multipart)

```dart
Future<SendMessageResponse> sendPhotoMessage(
  String matchId,
  String imagePath,
  String? caption,
  String authToken,
) async {
  final request = http.MultipartRequest(
    'POST',
    Uri.parse('https://api.bondi.ir/api/v1/messages/$matchId/photo'),
  );
  request.headers['Authorization'] = 'Bearer $authToken';
  request.fields['caption'] = caption ?? '';
  request.files.add(
    await http.MultipartFile.fromPath('file', imagePath),
  );

  final response = await request.send();
  final body = await response.stream.bytesToString();

  if (response.statusCode == 200) {
    return SendMessageResponse.fromJson(json.decode(body));
  }
  throw Exception('Failed to send photo: ${response.statusCode}');
}
```

### 10.5 Handling WebSocket Events in UI

```dart
class ChatScreen extends StatefulWidget {
  final String matchId;
  final String jwtToken;

  const ChatScreen({required this.matchId, required this.jwtToken, super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  late ChatWebSocketService _wsService;
  late TypingIndicatorController _typingController;
  List<Message> _messages = [];
  bool _isOtherUserOnline = false;
  bool _isTyping = false;

  @override
  void initState() {
    super.initState();
    _wsService = ChatWebSocketService(
      matchId: widget.matchId,
      jwtToken: widget.jwtToken,
    );
    _typingController = TypingIndicatorController();
    _wsService.connect();

    _wsService.messages.listen((event) {
      final type = event['type'] as String;
      final data = event['data'] as Map<String, dynamic>? ?? {};

      switch (type) {
        case 'new_message':
          _handleNewMessage(data);
          break;
        case 'typing':
          setState(() => _isTyping = true);
          break;
        case 'typing_stopped':
          setState(() => _isTyping = false);
          break;
        case 'user_online':
          setState(() => _isOtherUserOnline = true);
          break;
        case 'user_offline':
          setState(() => _isOtherUserOnline = false);
          break;
        case 'messages_read':
          _handleReadReceipts(data['message_ids'] as List);
          break;
      }
    });

    _wsService.connectionState.listen((connected) {
      if (!connected) {
        // Show offline indicator
      }
    });
  }

  void _handleNewMessage(Map<String, dynamic> data) {
    final message = Message.fromSocketData(data);
    setState(() {
      _messages.add(message);
    });

    // If this is a text message, mark as read via HTTP
    if (message.messageType == 'text') {
      _markMessagesRead([message.id]);
    }
  }

  Future<void> _markMessagesRead(List<Uuid> messageIds) async {
    await http.post(
      Uri.parse('https://api.bondi.ir/api/v1/messages/read'),
      headers: {
        'Authorization': 'Bearer ${widget.jwtToken}',
        'Content-Type': 'application/json',
      },
      body: json.encode({'message_ids': messageIds.map((id) => id.toString()).toList()}),
    );
  }

  void _handleReadReceipts(List dynamic messageIds) {
    setState(() {
      for (final idStr in messageIds) {
        final msg = _messages.firstWhere(
          (m) => m.id.toString() == idStr,
          orElse: () => Message.empty(),
        );
        if (msg.id != Uuid()) {
          msg.isRead = true;
        }
      }
    });
  }

  void _onSendButtonTap(String text) {
    _typingController.onMessageSent(_wsService);
    sendTextMessage(widget.matchId, text, widget.jwtToken);
    setState(() {
      _messages.add(Message.local(text, isMine: true));
    });
  }

  @override
  void dispose() {
    _typingController.dispose();
    _wsService.dispose();
    super.dispose();
  }
}
```

---

## 11. Error Handling

### WebSocket Close Codes

| Code | Reason | Action |
|------|--------|--------|
| `4001` | Unauthorized (invalid/expired JWT) | Redirect to login |
| `4003` | Access denied (not a match participant) | Show error, close chat |
| Other unexpected | Server error | Attempt reconnect with backoff |

### HTTP Error Codes for Message Endpoints

| Code | Meaning | Flutter Action |
|------|---------|----------------|
| 400 | Invalid input (missing fields, wrong format) | Show inline validation error |
| 401 | Unauthorized (expired token) | Refresh token, retry |
| 403 | Not a participant in this chat | Show "Chat not found" |
| 404 | Match/user not found | Show error |
| 429 | Rate limited | Show "Sending too fast" toast, wait |

---

## 12. Encryption Note

Messages are encrypted server-side using **AES-256-GCM** with keys derived per-match from `match_id + ENCRYPTION_SECRET` (PBKDF2, 100,000 iterations). The Flutter app **never needs to encrypt or decrypt** — it always receives plaintext content via the HTTP API (`GET /messages/{id}`) and the WebSocket push events. Encryption is transparent to the client.

---

## 13. Redis Keys Reference

Use these for debugging or monitoring:

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `online:{user_id}` | String | 60s | User is online if this key exists |
| `typing:{match_id}:{user_id}` | String | 5s | User is typing (auto-expires) |
| `ws:user:{user_id}` | Pub/Sub channel | — | Per-user event delivery |
| `ws:chat:{match_id}` | Pub/Sub channel | — | Per-match chat event delivery |

---

## 14. Message Type Summary Table

### What Flutter sends (WebSocket)

| Direction | `type` value | Client fields | Server action |
|-----------|-------------|---------------|---------------|
| Client → Server | `ping` | none | Reply with `pong`, refresh online TTL |
| Client → Server | `typing` | none | Publish typing event to match |
| Client → Server | `typing_stopped` | none | Clear typing indicator |
| Client → Server | `read` | `message_ids: list<uuid>` | Broadcast `messages_read` to other participant |

### What the server sends (WebSocket)

| `type` value | When | Key data fields |
|-------------|------|-----------------|
| `new_message` | Any message sent | `message_type`, `content`/`media_url`/`duration`, `sender_id`, `sent_at` |
| `new_match` | Match created | `match_id`, `user` (id, name, age, main_photo_url) |
| `user_online` | Other user connects to chat | `user_id` |
| `user_offline` | Other user disconnects | `user_id` |
| `typing` | Other user starts typing | `match_id`, `user_id` |
| `typing_stopped` | Other user stops typing | `match_id`, `user_id` |
| `messages_read` | Other user reads messages | `message_ids`, `reader_id` |
| `pong` | Response to client's ping | none |
