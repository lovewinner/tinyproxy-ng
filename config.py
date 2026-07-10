import logging
import os
from logging.handlers import RotatingFileHandler

logger = logging.getLogger(__name__)


def load_config(config_file: str = 'config.yaml') -> dict:
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
    if os.path.exists(config_file):
        user_config = None
        for enc in ('utf-8', 'gbk'):
            try:
                with open(config_file, 'r', encoding=enc) as f:
                    user_config = yaml.safe_load(f)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        try:
            if user_config:
                default_config.update(user_config)
            print(f"[OK] Loaded config: {config_file}")
            print(f"   Auth: {'Enabled' if default_config['auth_enabled'] else 'Disabled'}")
            print(f"   Username: {default_config['username']}")
            print(f"   Password: {'*' * len(default_config['password']) if default_config['password'] else '(empty)'}")
        except Exception as e:
            logger.error(f"Failed to load config file: {e}, using defaults")
    else:
        print(f"[!] Config file {config_file} not found, using default configuration")
        print(f"[*] Copy {config_file}.example and edit it")
    return default_config



def configure_logging(config, debug=False):
    file_handler = None
    log_file = config.get('log_file', '')
    if log_file:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=config.get('log_max_size', 10 * 1024 * 1024),
            backupCount=config.get('log_backup_count', 5),
            encoding='utf-8'
        )
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logging.getLogger().addHandler(file_handler)
        logger.info(f"Log file: {log_file}")

    if debug:
        config['log_level'] = 'DEBUG'
        logging.getLogger().setLevel(logging.DEBUG)
    elif 'log_level' in config:
        log_level = getattr(logging, config['log_level'].upper(), logging.INFO)
        logging.getLogger().setLevel(log_level)
    return file_handler
