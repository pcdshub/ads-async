from .constants import AdsDataType

try:
    import pytmc
except ImportError:
    pytmc = None


class Symbol:
    index_group: int
    data_type: AdsDataType

    def __init__(self):
        self.index_group = 0x113445
        self.data_type = AdsDataType.INT32
        self.value = 0

    def serialize(self):
        # self.data_type, self.value
        ...


class SymbolDatabase:
    def __init__(self):
        self.symbols = {'MAIN.scale': Symbol()}
        self.index_groups = {}
        self.handles = {}

    def get_handle_by_name(self, name):
        return self.symbols[name]


class TmcDatabase(SymbolDatabase):
    def __init__(self, tmc):
        super().__init__()

        if pytmc is None:
            raise RuntimeError('pytmc unavailable for .tmc file support')
