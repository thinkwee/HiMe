"""
Telegram Gateway module — bidirectional messaging between Hime and Telegram.

Provides:
  - TelegramGateway: main lifecycle manager
  - TelegramPoller: long-poll consumer for inbound messages
  - TelegramSender: async message sender
  - CommandParser / handlers: slash-command processing
"""
from .gateway import TelegramGateway
from .models import MessageEnvelope
from .sender import TelegramSender

__all__ = ["TelegramGateway", "TelegramSender", "MessageEnvelope"]
