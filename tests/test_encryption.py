# tests/test_encryption.py
import pytest
import base64
import os
from app.core.encryption import (
    derive_chat_key,
    encrypt_message,
    decrypt_message,
    encrypt_content_for_admin,
    decrypt_content_for_admin,
)
from app.core.config import settings


class TestEncryptionCore:
    """Test core encryption functions"""
    
    def test_derive_chat_key(self):
        """Test that chat key derivation works consistently"""
        match_id = "test_match_123"
        
        key1 = derive_chat_key(match_id)
        key2 = derive_chat_key(match_id)
        
        # Same match_id should produce same key
        assert key1 == key2
        assert len(key1) == 32  # 32 bytes = 256 bits for AES-256
        
    def test_derive_chat_key_different_match(self):
        """Test that different match_ids produce different keys"""
        match_id_1 = "test_match_123"
        match_id_2 = "test_match_456"
        
        key1 = derive_chat_key(match_id_1)
        key2 = derive_chat_key(match_id_2)
        
        # Different match_ids should produce different keys
        assert key1 != key2
        
    def test_encrypt_decrypt_basic(self):
        """Test basic encrypt/decrypt roundtrip"""
        match_id = "test_match_123"
        original_text = "Hello, this is a test message!"
        
        encrypted = encrypt_message(original_text, match_id)
        decrypted = decrypt_message(encrypted, match_id)
        
        assert encrypted != original_text
        assert decrypted == original_text
        
    def test_encrypt_decrypt_empty_string(self):
        """Test encryption of empty string"""
        match_id = "test_match_123"
        original_text = ""
        
        encrypted = encrypt_message(original_text, match_id)
        decrypted = decrypt_message(encrypted, match_id)
        
        assert encrypted == ""
        assert decrypted == ""
        
    def test_encrypt_decrypt_none(self):
        """Test encryption of None"""
        match_id = "test_match_123"
        
        encrypted = encrypt_message(None, match_id)
        decrypted = decrypt_message(encrypted, match_id)
        
        assert encrypted is None
        assert decrypted is None
        
    def test_encrypt_different_texts(self):
        """Test that different messages produce different encrypted strings"""
        match_id = "test_match_123"
        
        msg1 = "Hello world"
        msg2 = "Goodbye world"
        
        encrypted1 = encrypt_message(msg1, match_id)
        encrypted2 = encrypt_message(msg2, match_id)
        
        assert encrypted1 != encrypted2
        
    def test_encrypt_same_text_with_random_nonce(self):
        """Test that same text encrypts differently each time (random nonce)"""
        match_id = "test_match_123"
        original_text = "This is a test message"
        
        encrypted1 = encrypt_message(original_text, match_id)
        encrypted2 = encrypt_message(original_text, match_id)
        
        # Should be different due to random nonce
        assert encrypted1 != encrypted2
        
        # Both should decrypt to the same text
        decrypted1 = decrypt_message(encrypted1, match_id)
        decrypted2 = decrypt_message(encrypted2, match_id)
        
        assert decrypted1 == original_text
        assert decrypted2 == original_text
        
    def test_decrypt_wrong_key(self):
        """Test decryption with wrong match_id (should fail)"""
        match_id = "test_match_123"
        wrong_match_id = "test_match_456"
        original_text = "Secret message"
        
        encrypted = encrypt_message(original_text, match_id)
        
        # Decrypt with wrong match_id should raise exception
        with pytest.raises(Exception):
            decrypt_message(encrypted, wrong_match_id)
            
    def test_encrypt_long_message(self):
        """Test encryption of long messages"""
        match_id = "test_match_123"
        original_text = "A" * 10000  # 10,000 characters
        
        encrypted = encrypt_message(original_text, match_id)
        decrypted = decrypt_message(encrypted, match_id)
        
        assert decrypted == original_text
        
    def test_encrypt_unicode(self):
        """Test encryption of unicode/emoji characters"""
        match_id = "test_match_123"
        original_text = "سلام جهان! 🌍 Hello world! こんにちは"
        
        encrypted = encrypt_message(original_text, match_id)
        decrypted = decrypt_message(encrypted, match_id)
        
        assert decrypted == original_text
        
    def test_encrypt_special_characters(self):
        """Test encryption of special characters"""
        match_id = "test_match_123"
        original_text = "!@#$%^&*()_+{}|:<>?~`-=[]\;',./"
        
        encrypted = encrypt_message(original_text, match_id)
        decrypted = decrypt_message(encrypted, match_id)
        
        assert decrypted == original_text
        
    def test_decrypt_invalid_data(self):
        """Test decryption of invalid data"""
        match_id = "test_match_123"
        invalid_encrypted = "this_is_not_valid_base64"
        
        with pytest.raises(Exception):
            decrypt_message(invalid_encrypted, match_id)
            
    def test_admin_encrypt_decrypt(self):
        """Test admin encryption/decryption (same as regular)"""
        match_id = "test_match_123"
        original_text = "Admin test message"
        
        encrypted = encrypt_content_for_admin(original_text, match_id)
        decrypted = decrypt_content_for_admin(encrypted, match_id)
        
        assert encrypted != original_text
        assert decrypted == original_text


class TestEncryptionIntegration:
    """Test encryption integration with real-world scenarios"""
    
    def test_message_thread_encryption(self):
        """Test encrypting multiple messages in a chat thread"""
        match_id = "test_match_123"
        messages = [
            "First message",
            "Second message", 
            "Third message with emojis 🎉",
            "Fourth message with numbers 1234567890",
        ]
        
        encrypted_messages = []
        for msg in messages:
            encrypted = encrypt_message(msg, match_id)
            encrypted_messages.append(encrypted)
            
            # Each message should be unique
            for i in range(len(encrypted_messages) - 1):
                assert encrypted_messages[i] != encrypted
                
        # All should decrypt correctly
        for i, msg in enumerate(messages):
            decrypted = decrypt_message(encrypted_messages[i], match_id)
            assert decrypted == msg
            
    def test_different_match_ids(self):
        """Test that messages from different matches are isolated"""
        match_id_1 = "match_123"
        match_id_2 = "match_456"
        
        msg1 = "Message for match 1"
        msg2 = "Message for match 2"
        
        encrypted1 = encrypt_message(msg1, match_id_1)
        encrypted2 = encrypt_message(msg2, match_id_2)
        
        # Can't decrypt with wrong match
        with pytest.raises(Exception):
            decrypt_message(encrypted1, match_id_2)
            
        with pytest.raises(Exception):
            decrypt_message(encrypted2, match_id_1)
            
        # Can decrypt with correct match
        decrypted1 = decrypt_message(encrypted1, match_id_1)
        decrypted2 = decrypt_message(encrypted2, match_id_2)
        
        assert decrypted1 == msg1
        assert decrypted2 == msg2