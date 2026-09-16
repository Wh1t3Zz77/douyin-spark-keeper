import logging
import time

from .database import Base, SessionLocal, engine
from .mailer import process_outbox


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("spark-mail-worker")


def main() -> None:
    Base.metadata.create_all(engine)
    logger.info("mail worker started")
    while True:
        try:
            with SessionLocal() as db:
                processed = process_outbox(db)
            time.sleep(2 if processed else 10)
        except Exception:
            logger.exception("mail worker loop failed")
            time.sleep(10)


if __name__ == "__main__":
    main()
