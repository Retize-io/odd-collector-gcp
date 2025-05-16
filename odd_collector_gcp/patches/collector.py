import asyncio
import logging

from odd_collector_sdk.collector import Collector
from odd_collector_sdk.logger import logger
from odd_collector_sdk.shutdown import shutdown, shutdown_by

logging.getLogger("apscheduler.scheduler").setLevel(logging.ERROR)


class PatchedCollector(Collector):
    def start_polling(self):
        from datetime import datetime

        import tzlocal
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from odd_collector_sdk.job import create_job

        misfire_grace_time = (
            self.config.misfire_grace_time or self.config.default_pulling_interval * 60
        )
        scheduler = AsyncIOScheduler(
            timezone=str(tzlocal.get_localzone()), event_loop=self._loop
        )
        for adapter in self._adapters:
            scheduler.add_job(
                create_job(self._api, adapter, self.config.chunk_size).start,
                "interval",
                minutes=self.config.default_pulling_interval,
                next_run_time=datetime.now(),
                misfire_grace_time=misfire_grace_time,
                max_instances=self.config.max_instances,
                coalesce=True,
                id=adapter.config.name,
            )
        scheduler.start()

    def run(self, loop=None):
        if not loop:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
        try:
            import signal

            signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
            for s in signals:
                loop.add_signal_handler(
                    s, lambda s=s: asyncio.create_task(shutdown_by(s, loop))
                )

            loop.run_until_complete(self.register_data_sources())

            interval = self.config.default_pulling_interval
            logger.info(f"Config interval {interval=}")
            if not interval:
                logger.info("Collector will be run once.")
                loop.run_until_complete(self.one_time_run())
            else:
                self.start_polling()
                loop.run_forever()
        except Exception as e:
            import traceback

            logger.debug(traceback.format_exc())
            logger.error(e)
            loop.run_until_complete(shutdown(loop))
