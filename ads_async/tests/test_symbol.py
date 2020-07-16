import pathlib

import pytest

import ads_async

MODULE_PATH = pathlib.Path(__file__).parent


@pytest.fixture(scope='module')
def tmc_filename() -> str:
    return MODULE_PATH / 'kmono.tmc'


@pytest.fixture(scope='function')
def memory():
    return ads_async.symbols.PlcMemory(1000)


def test_symbol(memory):
    sym = ads_async.symbols.Symbol(
        data_area=None,
        name='test',
        offset=0,
        data_type=ads_async.constants.AdsDataType.INT32,
        array_length=1,
        memory=memory)

    value = 0x2345
    sym.write(value)
    assert sym.read().value == value
    assert sym.value.value == value


def test_tmc(tmc_filename):
    db = ads_async.symbols.TmcDatabase(tmc_filename)
    for data_area in db.data_areas:
        print()
        print(data_area)
        for _, symbol in data_area.symbols.items():
            print(symbol)
