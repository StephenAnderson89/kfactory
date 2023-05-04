import pytest
import kfactory as kf
from functools import partial


class LAYER_CLASS(kf.LayerEnum):
    WG = (1, 0)
    WGCLAD = (111, 0)


@pytest.fixture
def LAYER():
    return LAYER_CLASS


@pytest.fixture
def wg_enc(LAYER):
    return kf.utils.Enclosure(name="WGSTD", sections=[(LAYER.WGCLAD, 0, 2000)])


@pytest.fixture
def waveguide_factory(LAYER, wg_enc):
    return partial(kf.cells.dbu.waveguide, layer=LAYER.WG, enclosure=wg_enc)


@pytest.fixture
def waveguide(LAYER, wg_enc) -> kf.KCell:
    return kf.cells.waveguide.waveguide(
        width=0.5, length=1, layer=LAYER.WG, enclosure=wg_enc
    )


@pytest.fixture
def bend90(LAYER, wg_enc) -> kf.KCell:
    return kf.cells.circular.bend_circular(
        width=1, radius=10, layer=LAYER.WG, enclosure=wg_enc, theta=90
    )


@pytest.fixture
def bend180(LAYER, wg_enc) -> kf.KCell:
    return kf.cells.circular.bend_circular(
        width=1, radius=10, layer=LAYER.WG, enclosure=wg_enc, theta=180
    )


@pytest.fixture
def bend90_euler(LAYER, wg_enc) -> kf.KCell:
    return kf.cells.euler.bend_euler(
        width=1, radius=10, layer=LAYER.WG, enclosure=wg_enc, theta=90
    )


@pytest.fixture
def bend180_euler(LAYER, wg_enc) -> kf.KCell:
    return kf.cells.euler.bend_euler(
        width=1, radius=10, layer=LAYER.WG, enclosure=wg_enc, theta=180
    )


@pytest.fixture
def taper(LAYER, wg_enc) -> kf.KCell:
    return kf.cells.taper.taper(
        width1=0.5,
        width2=1,
        length=10,
        layer=LAYER.WG,
        enclosure=wg_enc,
    )


@pytest.fixture
def optical_port(LAYER):
    return kf.Port(
        name="o1",
        trans=kf.kdb.Trans.R0,
        layer=LAYER.WG,
        width=1000,
        port_type="optical",
    )


@pytest.fixture
def cells(bend90, bend180, bend90_euler, taper, waveguide) -> list[kf.KCell]:
    return [
        bend90,
        bend180,
        bend90_euler,
        taper,
        waveguide,
    ]


@pytest.fixture
def pdk(LAYER, waveguide_factory, wg_enc):
    pdk = kf.pdk.Pdk(layers=LAYER, name="TEST_PDK")
    pdk.register_cells(waveguide=waveguide_factory)
    pdk.register_enclosures(wg=wg_enc)
    pdk.activate()
    return pdk


# @pytest.fixture
# def wg():
#     return LAYER.WG
