import ctypes

from . import constants
from .constants import AdsDataType

try:
    import pytmc
except ImportError:
    pytmc = None


class PlcMemory:
    """
    Contiguous block of PLC memory, for use by symbols.
    """

    def __init__(self, size):
        self.memory = bytearray(size)

    def read(self, offset, size):
        return memoryview(self.memory)[offset:offset + size]

    def write(self, offset, data):
        size = len(data)
        self.memory[offset:offset + size] = data


class Symbol:
    index_group: int
    data_type: AdsDataType
    ctypes_data_type: type(ctypes.c_uint8)
    size: int
    array_length: int

    def __init__(self,
                 group: constants.AdsIndexGroup,
                 offset: int,
                 data_type: constants.AdsDataType,
                 array_length: int,
                 memory: PlcMemory):
        self.group = group
        self.offset = offset
        self.array_length = array_length
        self.data_type = data_type
        self.ctypes_data_type = (array_length *
                                 AdsDataType.to_ctypes[self.data_type])
        self.size = ctypes.sizeof(self.ctypes_data_type)
        self.memory = memory

    def read(self):
        raw = self.memory.read(self.offset, self.size)
        return self.ctypes_data_type.from_buffer(raw)

    def write(self, value):
        if not isinstance(value, type(ctypes.c_int)):
            value = self.ctypes_data_type(value)
        return self.memory.write(self.offset, bytes(value))

    @property
    def value(self):
        return self.read()

    def serialize(self):
        # self.data_type, self.value
        ...


class SymbolDatabase:
    def __init__(self):
        self.symbols = {'MAIN.scale': Symbol()}
        self.index_groups = {
            constants.AdsIndexGroup.PLC_DATA_AREA: {},
        }
        self.handles = {}

    def get_handle_by_name(self, name):
        return self.symbols[name]


class TmcDatabase(SymbolDatabase):
    def __init__(self, tmc):
        super().__init__()

        if pytmc is None:
            raise RuntimeError('pytmc unavailable for .tmc file support')
