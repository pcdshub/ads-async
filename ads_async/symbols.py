try:
    import pytmc
except ImportError:
    pytmc = None


class SymbolDatabase:
    def __init__(self):
        self.database = {}

    def get_handle_by_name(self, name):
        ...


class TmcDatabase(SymbolDatabase):
    def __init__(self, tmc):
        super().__init__()

        if pytmc is None:
            raise RuntimeError('pytmc unavailable for .tmc file support')
