import ctypes
import enum
import logging
import math
import sys
import typing

from . import constants, structs
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

    def __len__(self):
        return len(self.memory)

    def read(self, offset, size):
        return memoryview(self.memory)[offset:offset + size]

    def write(self, offset, data):
        size = len(data)
        self.memory[offset:offset + size] = data


class Symbol:
    name: str
    data_type: AdsDataType
    data_area: 'DataArea'
    byte_size: int
    array_length: int

    def __init__(self,
                 name: str,
                 offset: int,
                 data_type: constants.AdsDataType,
                 array_length: int,
                 data_area: 'DataArea'
                 ):
        self.array_length = array_length
        self.data_area = data_area
        self.data_type = data_type
        self.name = name
        self.offset = offset

        self._configure_data_type()

    @property
    def memory(self) -> PlcMemory:
        return self.data_area.memory

    def _configure_data_type(self):
        ctypes_base_type = self.data_type.ctypes_type
        if self.array_length > 1:
            self.ctypes_data_type = self.array_length * ctypes_base_type
        else:
            self.ctypes_data_type = ctypes_base_type
        self.byte_size = ctypes.sizeof(self.ctypes_data_type)

    def read(self):
        raw = self.memory.read(self.offset, self.byte_size)
        return self.ctypes_data_type.from_buffer(raw)

    def write(self, value):
        if not isinstance(value, ctypes._SimpleCData):
            if isinstance(value, bytes):
                consumed, value = structs.deserialize_data(
                    data_type=self.data_type, data=value,
                    length=self.array_length,
                )
            else:
                value = self.ctypes_data_type(value)

        logger.debug('Symbol %s write %s', self, value)
        return self.memory.write(self.offset, bytes(value))

    @property
    def value(self):
        return self.read()

    @property
    def memory_range(self) -> typing.Tuple[int, int]:
        return (self.offset, self.offset + self.byte_size + 1)

    def __repr__(self):
        mem_start, mem_end = self.memory_range
        return (
            f'<{self.__class__.__name__} {self.name!r} '
            f'[{mem_start}:{mem_end}] '
            f'value={self.value}>'
        )


def tmc_to_symbols(
        item: typing.Union['pytmc.parser.symbol',
                           'pytmc.parser.SubItem'],
        data_area: 'DataArea',
        *,
        parent_name: str = '',
        additional_offset: int = 0,
        ) -> typing.Generator['Symbol', None, None]:

    bit_offset = int(item.BitOffs[0].bit_offset)
    if (bit_offset % 8) != 0:
        raise ValueError('Symbol not byte-aligned?')

    offset = bit_offset // 8 + additional_offset
    data_type = item.data_type

    if parent_name:
        symbol_name = '.'.join((parent_name, item.name))
    else:
        symbol_name = item.name

    if not data_type.is_complex_type:
        ads_data_type = TmcTypes[data_type.name].value
        symbol = BasicSymbol(
            name=symbol_name,
            data_area=data_area,
            offset=offset,
            data_type=ads_data_type,
            array_length=data_type.length,
        )
        if hasattr(item, 'Comment'):
            symbol.__doc__ = item.Comment[0].text

        yield symbol
    else:
        # hack to just get first-level sub-items, including subclass (=extended
        # type) items
        sub_items = list(set(sub_item[1] for sub_item in item.walk()
                             if len(sub_item) > 1))
        sub_items.sort(key=lambda item: item.bit_offset)

        for sub_item in sub_items:
            yield from tmc_to_symbols(sub_item, data_area=data_area,
                                      parent_name=symbol_name,
                                      additional_offset=offset,
                                      )

        bit_size = int(item.BitSize[0].bit_size)
        if (bit_size % 8) != 0:
            raise ValueError('Symbol bits?')

        yield ComplexSymbol(
            name=item.name,
            data_area=data_area,
            offset=offset,
            data_type=AdsDataType.BIGTYPE,
            array_length=data_type.length,
            struct_size=bit_size // 8,
        )


class BasicSymbol(Symbol):
    """
    A symbol holding a basic data type value.

    This includes support for scalars and arrays of the following types:
    INT8 UINT8 INT16 UINT16 INT32 UINT32 INT64 UINT64 REAL32 REAL64 STRING
    WSTRING REAL80 BIT

    Notably, this does not include BIGTYPE, which is represented by
    :class:`ComplexSymbol`.
    """
    ctypes_data_type: typing.Union[typing.Type[ctypes.Array],
                                   typing.Type[ctypes._SimpleCData]]

    def _configure_data_type(self):
        ctypes_base_type = self.data_type.ctypes_type
        if self.array_length > 1:
            self.ctypes_data_type = self.array_length * ctypes_base_type
        else:
            self.ctypes_data_type = ctypes_base_type
        self.byte_size = ctypes.sizeof(self.ctypes_data_type)


class ComplexSymbol(Symbol):
    def __init__(self, *, struct_size: int, **kwargs):
        self._struct_size = struct_size
        super().__init__(**kwargs)

    def _configure_data_type(self):
        self.byte_size = self._struct_size * self.array_length
        self.ctypes_data_type = (
            (ctypes.c_ubyte * self._struct_size) * self.array_length
        )


class DataArea:
    memory: PlcMemory
    index_group: constants.AdsIndexGroup
    symbols: typing.Dict[str, Symbol]
    area_type: str

    def __init__(self, index_group: constants.AdsIndexGroup,
                 area_type: str,
                 *,
                 memory: PlcMemory = None,
                 memory_size: typing.Optional[int] = None):

        self.index_group = index_group
        self.area_type = area_type
        self.symbols = {}

        if memory is not None:
            self.memory = memory
        elif memory_size is not None:
            self.memory = PlcMemory(memory_size)
        else:
            raise ValueError('Must specify either memory or memory_size')


class TmcDataArea(DataArea):
    def add_symbols(self, tmc_symbol: 'pytmc.parser.Symbol'):
        for symbol in tmc_to_symbols(tmc_symbol,
                                     data_area=self):  # type: Symbol
            self.symbols[symbol.name] = symbol

    def __repr__(self):
        return f'<{self.__class__.__name__} {self.area_type}>'


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

    LINT = AdsDataType.INT64
    ULINT = AdsDataType.UINT64

    REAL = AdsDataType.REAL32
    LREAL = AdsDataType.REAL64

    STRING = AdsDataType.STRING
    COMPLEX = AdsDataType.BIGTYPE


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


class Database:
    data_areas: typing.List[DataArea]
    index_groups: typing.Dict[constants.AdsIndexGroup, DataArea]

    def get_symbol_by_name(self, symbol_name) -> Symbol:
        raise KeyError(symbol_name)


class TmcDatabase(Database):
    tmc: 'pytmc.parser.TcModuleClass'

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

    def get_symbol_by_name(self, symbol_name: str) -> Symbol:
        for data_area in self.data_areas:
            try:
                return data_area.symbols[symbol_name]
            except KeyError:
                ...

        raise KeyError(symbol_name)

    def _load_data_areas(self):
        for tmc_area in self.tmc.find(pytmc.parser.DataArea):
            info = tmc_area.AreaNo[0].attributes
            area_type = info['AreaType']
            create_symbols = info.get('CreateSymbols', 'true')
            byte_size = int(tmc_area.ByteSize[0].text)
            if create_symbols != 'true':
                continue

            index_group = DataAreaIndexGroup[area_type]
            area = self.add_data_area(
                index_group, TmcDataArea(index_group, area_type,
                                         memory_size=byte_size))

            for sym in tmc_area.find(pytmc.parser.Symbol):
                area.add_symbols(sym)

        self._configure_plc_memory_area()

    def add_data_area(self, index_group: constants.AdsIndexGroup,
                      area: DataArea) -> DataArea:
        self.data_areas.append(area)
        self.index_groups[index_group] = area
        return area

    def _configure_plc_memory_area(self):
        if constants.AdsIndexGroup.PLC_MEMORY_AREA in self.index_groups:
            return

        index_group = constants.AdsIndexGroup.PLC_MEMORY_AREA
        self.add_data_area(
            index_group, DataArea(index_group, 'PLC_MEMORY_AREA',
                                  memory_size=100_000))


def map_symbols_in_memory(
        memory: PlcMemory, symbols: typing.List[Symbol]
        ) -> typing.Dict[int, typing.List[Symbol]]:
    """
    Map out memory indicating where Symbols are located.

    Not memory or time efficient; call sparingly.

    Parameters
    ----------
    memory : PlcMemory
        The memory backing the given symbols.

    symbols : list of Symbol
        The symbols in memory.

    Returns
    -------
    dict
        {byte_offset: [list of symbols]}
    """
    byte_to_symbol = {idx: [] for idx in range(len(memory))}

    def symbol_sort(sym):
        # In order of memory location, in reverse size (parent symbol overlap
        # with all its members -> keep parent symbol first)
        return (sym.memory_range[0], -sym.byte_size)

    symbols = list(sorted(symbols, key=symbol_sort))
    for sym in symbols:
        for offset in range(*sym.memory_range):
            byte_to_symbol[offset].append(sym)
    return byte_to_symbol


def dump_memory(
        memory: PlcMemory, symbols: typing.List[Symbol],
        file=sys.stdout,
        ) -> typing.Dict[int, typing.List[Symbol]]:
    """
    Map out memory indicating where Symbols are located and print to `file`.

    Not memory or time efficient; call sparingly.

    Parameters
    ----------
    memory : PlcMemory
        The memory backing the given symbols.

    symbols : list of Symbol
        The symbols in memory.

    file : file-like object, optional
        Defaults to standard output.

    Returns
    -------
    dict
        {byte_offset: [list of symbols]}
    """
    mapped = map_symbols_in_memory(memory, symbols)
    last_offset = -1

    zeros = int(math.log10(len(memory))) + 1
    memory_format = '{:>0%d}' % (zeros, )
    empty_format = f'{memory_format} ~ {memory_format} | ...'
    filled_format = f'{memory_format} | {{items}}'

    symbol_format = '{symbol.name} '
    spacer = '\n   ' + ' ' * zeros

    for offset, items in sorted(mapped.items()):
        if items:
            if offset - last_offset > 1:
                print(empty_format.format(last_offset + 1, offset - 1),
                      file=file)
            last_offset = offset
            items = spacer.join(symbol_format.format(symbol=symbol)
                                for symbol in items)
            print(filled_format.format(offset, items=items))

    if len(memory) - last_offset > 1:
        print(empty_format.format(last_offset + 1, len(memory) - 1),
              file=file)
