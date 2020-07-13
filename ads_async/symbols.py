try:
    import pytmc
except ImportError:
    pytmc = None


class SymbolDatabase:
    def __init__(self):
        ...

    @classmethod
    def from_tmc_file(cls, tmc):
        if pytmc is None:
            raise RuntimeError('pytmc unavailable for .tmc file support')


