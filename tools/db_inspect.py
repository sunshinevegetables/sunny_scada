import sqlite3
import os
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

def main():
    p = os.getenv('DATABASE_URL') or 'sqlite:///sunny_scada.db'
    if p.startswith('sqlite:///'):
        fp = p.replace('sqlite:///','')
    else:
        fp = p
    logger.info('DB file: %s', fp)
    path = Path(fp)
    if not path.exists():
        logger.error('DB does not exist')
        return
    conn = sqlite3.connect(fp)
    cur = conn.cursor()
    for tbl in ('cfg_containers','cfg_equipment'):
        try:
            cur.execute(f"PRAGMA table_info({tbl})")
            rows = cur.fetchall()
            logger.info('Table %s', tbl)
            for r in rows:
                logger.info('%s', r)
        except Exception as e:
            logger.exception('Error reading table %s: %s', tbl, e)
    conn.close()

if __name__ == '__main__':
    main()
