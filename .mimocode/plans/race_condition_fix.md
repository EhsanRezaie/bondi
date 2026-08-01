# Race Condition Fix Plan
## Bondi Dating App — Backend

> Comprehensive plan to eliminate all race conditions in the codebase.

---

## Summary of All Race Conditions

| ID | File | Pattern | Severity | Description |
|----|------|---------|----------|-------------|
| RC-1 | `auth.py` register_verify | Check-then-act (unique constraint) | High | Duplicate email on concurrent registration |
| RC-2 | `auth.py` google_auth | Check-then-act (unique constraint) | High | Duplicate email on concurrent Google login |
| RC-3 | `auth.py` generate_referral_code | Read-then-write (unique constraint) | Medium | Duplicate referral codes |
| RC-4 | `redis.py` verify_code_with_attempts | Non-atomic read-modify-write | Medium | OTP attempt counter under-counts |
| RC-5 | `swipes.py` match creation | Check-then-act (no IntegrityError handler) | Critical | Unhandled crash on concurrent mutual likes |
| RC-6 | `swipes.py` swipe insert | Check-then-act (partially mitigated) | Fixed | Already fixed with IntegrityError handler |
| RC-7 | `users.py` update_me | Read-modify-write (lost update) | High | Profile updates overwrite each other |
| RC-8 | `users.py` update_settings | Read-modify-write (lost update) | High | Settings updates overwrite each other |
| RC-9 | `users.py` update_interests | Delete-then-insert (non-atomic) | Medium | Interests lost on concurrent updates |
| RC-10 | `users.py` update_prompts | Delete-then-insert (non-atomic) | Medium | Prompts lost on concurrent updates |
| RC-11 | `blocks.py` block_user | Check-then-act (unique constraint) | Medium | Duplicate block on concurrent requests |
| RC-12 | `messages.py` new chat limit | Check-then-act across service boundary | Medium | Multiple new chats to same target |
| RC-13 | `messages.py` unmatched msg limit | Check-then-act across service boundary | Medium | Exceeds 2-msg unmatched limit |
| RC-14 | `notifications.py` register_device_token | Check-then-act (unique constraint) | Medium | Duplicate token on concurrent registration |
| RC-15 | `referrals.py` claim_referral | Check-then-act (unique constraint) | High | Duplicate referral claim crash |
| RC-16 | `subscriptions.py` verify_payment | Read-modify-write (lost update) | High | Double premium days on concurrent payment |
| RC-17 | `admin_users.py` grant_premium | Read-modify-write (lost update) | Medium | Double premium days from concurrent admin grants |
| RC-18 | `system.py` version overrides | Read-modify-write on file (non-atomic) | High | Admin config changes lost on concurrent updates |
| RC-19 | `system.py` maintenance mode | Read-modify-write on file (non-atomic) | Medium | Maintenance state corrupted |
| RC-20 | `cache.py` pop_discover_stack | Non-atomic LRANGE+LTRIM | Medium | Duplicate discover cards |
| RC-21 | `chat_service.py` can_start_new_chat | Check-then-act across service boundary | Medium | Same as RC-12 at service layer |
| RC-22 | `chat_service.py` check_unmatched_message_limit | Check-then-act across service boundary | Medium | Same as RC-13 at service layer |
| RC-23 | `reward_service.py` premium_until read-modify-write | Read-modify-write | Medium | Same as RC-16 but in service layer |

---

## Phase 1 — Critical (prevent crashes and data corruption)

### P1.1: IntegrityError handler for match creation (RC-5)
- File: `app/api/v1/endpoints/swipes.py`
- Wrap match creation (session.add + session.flush) in try/except IntegrityError
- On UniqueViolationError: rollback, return 409 "Already matched"

### P1.2: IntegrityError handler for registration (RC-1)
- File: `app/api/v1/endpoints/auth.py`
- Wrap user creation in register_verify in try/except IntegrityError
- On unique violation: rollback, return 409

### P1.3: IntegrityError handler for Google auth (RC-2)
- File: `app/api/v1/endpoints/auth.py`
- Wrap user creation in google_auth in try/except IntegrityError

### P1.4: IntegrityError handler for block_user (RC-11)
- File: `app/api/v1/endpoints/blocks.py`
- Wrap block creation in try/except IntegrityError

### P1.5: IntegrityError handler for register_device_token (RC-14)
- File: `app/api/v1/endpoints/notifications.py`
- Wrap token creation in try/except IntegrityError

### P1.6: IntegrityError handler for claim_referral (RC-15)
- File: `app/api/v1/endpoints/referrals.py`
- Wrap reward creation in try/except IntegrityError

### P1.7: FOR UPDATE on premium_until in verify_payment (RC-16)
- File: `app/api/v1/endpoints/subscriptions.py`
- Add `.with_for_update()` when reading user before modifying premium_until

### P1.8: FOR UPDATE on premium_until in admin_grant_premium (RC-17)
- File: `app/api/v1/endpoints/admin_users.py`
- Add `.with_for_update()` when reading user before modifying premium_until

### P1.9: FOR UPDATE on profile update (RC-7)
- File: `app/api/v1/endpoints/users.py`
- Add `.with_for_update()` when reading profile before modifying

### P1.10: FOR UPDATE on settings update (RC-8)
- File: `app/api/v1/endpoints/users.py`
- Add `.with_for_update()` when reading settings before modifying

---

## Phase 2 — High (prevent silent data loss)

### P2.1: Atomic file writes for system.py (RC-18, RC-19)
- File: `app/api/v1/endpoints/system.py`
- Use tempfile + os.replace for atomic writes

### P2.2: FOR UPDATE on interests/prompts replace (RC-9, RC-10)
- File: `app/api/v1/endpoints/users.py`
- Lock user row before delete+insert

---

## Phase 3 — Medium (hardening)

### P3.1: Atomic OTP attempt counter (RC-4)
- File: `app/core/redis.py`
- Use Redis INCR atomically instead of read-modify-write

### P3.2: Atomic discover stack pop (RC-20)
- File: `app/core/cache.py`
- Use Lua script for LRANGE+LTRIM

### P3.3: DB constraint for chat limits (RC-12, RC-13, RC-21, RC-22)
- File: Alembic migration + `chat_service.py`
- Partial unique index on messages for unmatched chats

### P3.4: FOR UPDATE in reward_service.py (RC-23)
- File: `app/services/reward_service.py`
- Add `.with_for_update()` on premium_until read-modify-write

---

## Phase 4 — Low (benign)

### P4.1: RC-6 — Already fixed
### P4.2: RC-22 — Acceptable stale cache
### P4.3: RC-42 — Acceptable cache stampede