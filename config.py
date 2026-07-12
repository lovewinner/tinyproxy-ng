from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os

logger = logging.getLogger(__name__)


def load_config(config_file: str = 'config.yaml') -> dict[str, object]:
    import yaml
    default_config = {
        'host': '0.0.0.0',
        'port': 8080,
        'username': 'admin',
        'password': 'password123',
        'auth_enabled': True,
        'log_level': 'INFO',
        'upstream_proxies': {},
        'max_connections': 500,
    }
    
    # Load from YAML file if exists
    if os.path.exists(config_file):
        user_config = None
        for enc in ('utf-8', 'gbk'):
            try:
                with open(config_file, encoding=enc) as f:
                    user_config = yaml.safe_load(f)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        try:
            if user_config:
                default_config.update(user_config)
            logger.info(f"Loaded config: {config_file}")
        except Exception as e:
            logger.error(f"Failed to load config file: {e}, using defaults")
    else:
        logger.info(f"Config file {config_file} not found, using default configuration")
        logger.info(f"Copy {config_file}.example and edit it")
    
    # Environment variables override config file (for Docker support)
    if os.getenv('PROXY_HOST'):
        default_config['host'] = os.getenv('PROXY_HOST')
    if os.getenv('PROXY_PORT'):
        try:
            default_config['port'] = int(os.getenv('PROXY_PORT', '8080'))
        except ValueError:
            logger.warning(f"Invalid PROXY_PORT value: {os.getenv('PROXY_PORT')}")
    if os.getenv('AUTH_ENABLED'):
        default_config['auth_enabled'] = os.getenv('AUTH_ENABLED', 'true').lower() in ('true', '1', 'yes')
    if os.getenv('USERNAME'):
        default_config['username'] = os.getenv('USERNAME')
    if os.getenv('PASSWORD'):
        default_config['password'] = os.getenv('PASSWORD')
    if os.getenv('LOG_LEVEL'):
        default_config['log_level'] = os.getenv('LOG_LEVEL', 'INFO').upper()
    if os.getenv('UPSTREAM_PROXY_HTTP'):
        default_config.setdefault('upstream_proxies', {})['http'] = os.getenv('UPSTREAM_PROXY_HTTP')
    if os.getenv('UPSTREAM_PROXY_HTTPS'):
        default_config.setdefault('upstream_proxies', {})['https'] = os.getenv('UPSTREAM_PROXY_HTTPS')
    if os.getenv('MAX_CONNECTIONS'):
        try:
            default_config['max_connections'] = int(os.getenv('MAX_CONNECTIONS', '500'))
        except ValueError:
            logger.warning(f"Invalid MAX_CONNECTIONS value: {os.getenv('MAX_CONNECTIONS')}")
    
    logger.info(f"   Auth: {'Enabled' if default_config['auth_enabled'] else 'Disabled'}")
    logger.info(f"   Username: {default_config['username']}")
    logger.info(f"   Password: {'*' * len(str(default_config['password'])) if default_config['password'] else '(empty)'}")
    
    return default_config



def configure_logging(config: dict[str, object], debug: bool = False) -> RotatingFileHandler | None:
    file_handler: RotatingFileHandler | None = None
    log_file: str = str(config.get('log_file', ''))
    if log_file:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=int(str(config.get('log_max_size', 10 * 1024 * 1024))),
            backupCount=int(str(config.get('log_backup_count', 5))),
            encoding='utf-8'
        )
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logging.getLogger().addHandler(file_handler)
        logger.info(f"Log file: {log_file}")

    if debug:
        config['log_level'] = 'DEBUG'
        logging.getLogger().setLevel(logging.DEBUG)
    elif 'log_level' in config:
        log_level = getattr(logging, str(config['log_level']).upper(), logging.INFO)
        logging.getLogger().setLevel(log_level)
    return file_handler
