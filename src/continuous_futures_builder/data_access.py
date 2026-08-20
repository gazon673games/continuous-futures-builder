"""Public facade for the split data access module."""

from continuous_futures_builder._data_access_local import (
    LocalParquetDataAccess as LocalParquetDataAccess,
)
from continuous_futures_builder._data_access_protocol import (
    ContinuousFuturesDataAccess as ContinuousFuturesDataAccess,
)
from continuous_futures_builder._data_access_protocol import (
    _read_parquet_parts as _read_parquet_parts,
)
from continuous_futures_builder._data_access_store import (
    OhlcvDataStoreAccess as OhlcvDataStoreAccess,
)
from continuous_futures_builder._data_access_store import (
    _data_store_unavailable_error as _data_store_unavailable_error,
)
from continuous_futures_builder._data_access_store import (
    _load_data_store_class as _load_data_store_class,
)
from continuous_futures_builder._data_access_store import create_data_access as create_data_access
