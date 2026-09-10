"""Configuration management for Telegram Selfbot Maker."""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def load_config() -> dict:
    """Load and validate configuration from environment variables.
    
    Returns:
        dict: Configuration dictionary with BOT_TOKEN, API_ID, API_HASH
        
    Raises:
        ValueError: If required environment variables are missing
    """
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path)

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    api_id = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()

    missing_vars = []
    if not bot_token:
        missing_vars.append("BOT_TOKEN")
    if not api_id:
        missing_vars.append("API_ID")
    if not api_hash:
        missing_vars.append("API_HASH")

    if missing_vars:
        error_msg = (
            f"Missing required environment variables: {', '.join(missing_vars)}\n"
            f"Please copy .env.example to .env and fill in your values."
        )
        logger.error(error_msg)
        raise ValueError(error_msg)

    try:
        api_id_int = int(api_id)
    except ValueError:
        error_msg = f"API_ID must be an integer, got: {api_id}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    return {
        "BOT_TOKEN": bot_token,
        "API_ID": api_id_int,
        "API_HASH": api_hash,
    }


if __name__ == "__main__":
    try:
        config = load_config()
        print("✓ Configuration loaded successfully")
    except ValueError as e:
        print(f"✗ Configuration error: {e}")
