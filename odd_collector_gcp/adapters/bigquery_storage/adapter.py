import concurrent.futures
import logging

from google.cloud import bigquery
from odd_collector_sdk.domain.adapter import BaseAdapter
from odd_models.models import DataEntityList
from oddrn_generator.generators import BigQueryStorageGenerator

from odd_collector_gcp.adapters.bigquery_storage.dto import BigQueryDataset
from odd_collector_gcp.adapters.bigquery_storage.mapper import BigQueryStorageMapper
from odd_collector_gcp.domain.plugin import BigQueryStoragePlugin

logger = logging.getLogger(__name__)


class Adapter(BaseAdapter):
    config: BigQueryStoragePlugin
    generator: BigQueryStorageGenerator

    def __init__(self, config: BigQueryStoragePlugin):
        super().__init__(config)
        self.client = bigquery.Client(project=config.project)
        self.mapper = BigQueryStorageMapper(oddrn_generator=self.generator)

    def create_generator(self) -> BigQueryStorageGenerator:
        return BigQueryStorageGenerator(
            google_cloud_settings={"project": self.config.project},
        )

    def get_data_source_oddrn(self) -> str:
        return self.generator.get_data_source_oddrn()

    def get_data_entity_list(self) -> DataEntityList:
        logger.info(f"Fetching datasets from project: {self.config.project}...")
        start_time = __import__("time").time()

        # Fetch datasets with all the optimizations
        datasets = self.__fetch_datasets()

        # Map the datasets to entities
        logger.info("Mapping datasets to data entities...")
        entities = self.mapper.map_datasets(datasets)

        elapsed_time = __import__("time").time() - start_time
        logger.info(
            f"Completed fetching and mapping {len(entities)} entities in {elapsed_time:.2f} seconds"
        )

        return DataEntityList(
            data_source_oddrn=self.get_data_source_oddrn(), items=entities
        )

    def __fetch_datasets(self) -> list[BigQueryDataset]:
        datasets = []
        dataset_count = 0
        dataset_limit = self.config.max_datasets

        # List and filter datasets
        logger.info("Listing BigQuery datasets...")
        datasets_iterator = self.client.list_datasets(page_size=self.config.page_size)

        filtered_datasets = []
        for datasets_page in datasets_iterator.pages:
            logger.debug("Processing datasets page")
            for dr in datasets_page:
                if self.config.datasets_filter.is_allowed(dr.dataset_id):
                    filtered_datasets.append(dr)
                    dataset_count += 1
                    if dataset_limit and dataset_count >= dataset_limit:
                        logger.info(f"Reached maximum dataset limit: {dataset_limit}")
                        break

            if dataset_limit and dataset_count >= dataset_limit:
                break

        logger.info(f"Found {len(filtered_datasets)} datasets after filtering")

        # Process datasets (either in parallel or sequentially)
        if self.config.parallel_processing and len(filtered_datasets) > 1:
            # Calculate optimal number of workers based on dataset count
            max_workers = min(len(filtered_datasets), 10)  # Limit to 10 workers max
            logger.info(
                f"Processing {len(filtered_datasets)} datasets in parallel with {max_workers} workers"
            )

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=max_workers
            ) as executor:
                future_to_dataset = {
                    executor.submit(self.__process_dataset, dr): dr
                    for dr in filtered_datasets
                }
                for future in concurrent.futures.as_completed(future_to_dataset):
                    dr = future_to_dataset[future]
                    try:
                        dataset_result = future.result()
                        if dataset_result:
                            datasets.append(dataset_result)
                    except Exception as e:
                        logger.error(
                            f"Error processing dataset {dr.dataset_id}: {str(e)}"
                        )
        else:
            logger.info(f"Processing {len(filtered_datasets)} datasets sequentially")
            for dr in filtered_datasets:
                try:
                    dataset_result = self.__process_dataset(dr)
                    if dataset_result:
                        datasets.append(dataset_result)
                except Exception as e:
                    logger.error(f"Error processing dataset {dr.dataset_id}: {str(e)}")

        logger.info(f"Completed processing {len(datasets)} datasets")
        return datasets

    def __process_dataset(self, dataset_ref) -> BigQueryDataset:
        """
        Process a single dataset by fetching its tables
        """
        logger.info(f"Processing dataset: {dataset_ref.dataset_id}")
        try:
            dataset = self.client.get_dataset(dataset_ref.dataset_id)
            tables = []

            # Get tables with pagination and filtering
            tables_iterator = self.client.list_tables(
                dataset_ref, page_size=self.config.page_size
            )
            table_count = 0
            table_limit = self.config.max_tables_per_dataset

            for tables_page in tables_iterator.pages:
                for table_ref in tables_page:
                    # Apply table filter if configured
                    if (
                        self.config.tables_filter
                        and not self.config.tables_filter.is_allowed(table_ref.table_id)
                    ):
                        continue

                    try:
                        table = self.client.get_table(table_ref)
                        tables.append(table)
                        table_count += 1
                        if table_limit and table_count >= table_limit:
                            logger.debug(
                                f"Reached table limit ({table_limit}) for dataset {dataset_ref.dataset_id}"
                            )
                            break
                    except Exception as e:
                        logger.warning(
                            f"Error fetching table {table_ref.table_id}: {str(e)}"
                        )

                if table_limit and table_count >= table_limit:
                    break

            logger.info(
                f"Found {len(tables)} tables in dataset {dataset_ref.dataset_id}"
            )
            return BigQueryDataset(data_object=dataset, tables=tables)

        except Exception as e:
            logger.error(f"Error processing dataset {dataset_ref.dataset_id}: {str(e)}")
            return None
