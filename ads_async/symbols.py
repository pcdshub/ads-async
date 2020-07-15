import ctypes
import enum
import logging

from . import constants
from .constants import AdsDataType

try:
    import pytmc
except ImportError:
    pytmc = None


logger = logging.getLogger(__name__)


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
    name: str
    data_type: AdsDataType
    ctypes_data_type: type(ctypes.c_uint8)
    size: int
    array_length: int

    def __init__(self,
                 name: str,
                 offset: int,
                 data_type: constants.AdsDataType,
                 array_length: int,
                 memory: PlcMemory):
        self.name = name
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


class TmcDataArea:
    def __init__(self, area_type, *, memory: PlcMemory = None,
                 memory_size=None):
        self.area_type = area_type
        self.symbols = {}

        if memory is not None:
            self.memory = memory
        elif memory_size is not None:
            self.memory = PlcMemory(memory_size)
        else:
            raise ValueError('Must specify either memory or memory_size')

    def add_symbol(self, tmc_symbol):
        info = tmc_symbol.info
        bit_offset = int(info['bit_offs'])
        if (bit_offset % 8) != 0:
            logger.warning('Symbol not byte-aligned?')
            return

        offset = bit_offset // 8
        type_name = info['type']
        if type_name.startswith('STRING') and '(' in type_name:
            array_length = int(type_name.split('(')[1].rstrip(')'))
            type_name = 'STRING'
        else:
            array_length = getattr(tmc_symbol.array_info, 'elements', 1)

        try:
            data_type = TmcTypes[type_name].value
        except KeyError:
            logger.warning('Complex types not yet supported: %s', info['type'])
            # assert tmc_symbol.data_type.is_complex_type
            return

        symbol = self.symbols[tmc_symbol.name] = Symbol(
            tmc_symbol.name,
            offset=offset,
            data_type=data_type,
            array_length=array_length,
            memory=self.memory
        )
        return symbol

    def __repr__(self):
        return f'<DataArea {self.area_type}>'


class TmcTypes(enum.Enum):
    BOOL = AdsDataType.BIT
    BYTE = AdsDataType.UINT8
    SINT = AdsDataType.INT8
    USINT = AdsDataType.UINT8

    WORD = AdsDataType.UINT16
    INT = AdsDataType.INT16
    UINT = AdsDataType.UINT16

    DWORD = AdsDataType.UINT32
    DINT = AdsDataType.INT32
    UDINT = AdsDataType.UINT32

    ENUM = AdsDataType.UINT32

    REAL = AdsDataType.REAL32
    LREAL = AdsDataType.REAL64

    STRING = AdsDataType.STRING


class DataAreaIndexGroup(enum.Enum):
    # <AreaNo AreaType="Internal" CreateSymbols="true">3</AreaNo>
    # e.g., Global_Version.stLibVersion_Tc2_System
    Internal = constants.AdsIndexGroup.PLC_DATA_AREA  # 0x4040

    # <AreaNo AreaType="InputDst" CreateSymbols="true">0</AreaNo>
    # read/write input byte(s)
    InputDst = constants.AdsIndexGroup.IOIMAGE_RWIB  # 0xF020

    # <AreaNo AreaType="OutputSrc" CreateSymbols="true">1</AreaNo>
    # read/write output byte(s)
    # e.g., Axis.PlcToNc
    OutputSrc = constants.AdsIndexGroup.IOIMAGE_RWOB  # 0xF030

    # TODO:
    # Standard
    # InputSrc
    # OutputDst
    # MArea
    # RetainSrc
    # RetainDst
    # InfoData
    # RedundancySrc
    # RedundancyDst


class TmcDatabase:
    def __init__(self, tmc):
        super().__init__()

        if pytmc is None:
            raise RuntimeError('pytmc unavailable for .tmc file support')

        if not isinstance(tmc, pytmc.parser.TcModuleClass):
            logger.debug('Loading tmc file: %s', tmc)
            tmc = pytmc.parser.parse(tmc)

        self.tmc = tmc
        self.data_areas = []
        self.index_groups = {}
        self._load_data_areas()

    def _load_data_areas(self):
        for tmc_area in self.tmc.find(pytmc.parser.DataArea):
            info = tmc_area.AreaNo[0].attributes
            area_type = info['AreaType']
            create_symbols = info.get('CreateSymbols', 'true')
            byte_size = int(tmc_area.ByteSize[0].text)
            if create_symbols != 'true':
                continue

            area = TmcDataArea(area_type, memory_size=byte_size)
            self.data_areas.append(area)
            self.index_groups[DataAreaIndexGroup[area_type]] = area

            for sym in tmc_area.find(pytmc.parser.Symbol):
                area.add_symbol(sym)
