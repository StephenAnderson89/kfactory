"""Utilities for Ports.

Mainly renaming functions
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import (
    Callable,
    Iterable,
)
from enum import IntEnum, IntFlag, auto
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Literal,
    Self,
    overload,
)

import klayout.db as kdb
import klayout.rdb as rdb
from pydantic import (
    BaseModel,
    model_serializer,
)
from ruamel.yaml.constructor import BaseConstructor
from typing_extensions import TypedDict

from .conf import config
from .cross_section import CrossSectionSpec, SymmetricalCrossSection
from .layer import LayerEnum
from .settings import Info
from .typings import TPort, TUnit
from .utilities import pprint_ports

if TYPE_CHECKING:
    from .kcell import KCell, ProtoTKCell
    from .layout import KCLayout


def create_port_error(
    p1: ProtoPort[Any],
    p2: ProtoPort[Any],
    c1: ProtoTKCell[Any],
    c2: ProtoTKCell[Any],
    db: rdb.ReportDatabase,
    db_cell: rdb.RdbCell,
    cat: rdb.RdbCategory,
    dbu: float,
) -> None:
    it = db.create_item(db_cell, cat)
    if p1.name and p2.name:
        it.add_value(f"Port Names: {c1.name}.{p1.name}/{c2.name}.{p2.name}")
    it.add_value(
        port_polygon(p1.cross_section.width).transformed(p1.trans).to_dtype(dbu)
    )
    it.add_value(
        port_polygon(p2.cross_section.width).transformed(p2.trans).to_dtype(dbu)
    )


class PortCheck(IntFlag):
    opposite = auto()
    width = auto()
    layer = auto()
    port_type = auto()
    all_opposite = opposite + width + port_type + layer  # type: ignore[operator]
    all_overlap = width + port_type + layer  # type: ignore[operator]


def port_check(p1: Port, p2: Port, checks: PortCheck = PortCheck.all_opposite) -> None:
    if checks & PortCheck.opposite:
        assert (
            p1.trans == p2.trans * kdb.Trans.R180
            or p1.trans == p2.trans * kdb.Trans.M90
        ), f"Transformations of ports not matching for opposite check{p1=} {p2=}"
    if (checks & PortCheck.opposite) == 0:
        assert p1.trans == p2.trans or p1.trans == p2.trans * kdb.Trans.M0, (
            f"Transformations of ports not matching for overlapping check {p1=} {p2=}"
        )
    if checks & PortCheck.width:
        assert p1.width == p2.width, f"Width mismatch for {p1=} {p2=}"
    if checks & PortCheck.layer:
        assert p1.layer == p2.layer, f"Layer mismatch for {p1=} {p2=}"
    if checks & PortCheck.port_type:
        assert p1.port_type == p2.port_type, f"Port type mismatch for {p1=} {p2=}"


class BasePortDict(TypedDict):
    name: str | None
    kcl: KCLayout
    cross_section: SymmetricalCrossSection
    trans: kdb.Trans | None
    dcplx_trans: kdb.DCplxTrans | None
    info: Info
    port_type: str


class BasePort(BaseModel, arbitrary_types_allowed=True):
    name: str | None
    kcl: KCLayout
    cross_section: SymmetricalCrossSection
    trans: kdb.Trans | None = None
    dcplx_trans: kdb.DCplxTrans | None = None
    info: Info = Info()
    port_type: str

    def __copy__(self) -> BasePort:
        return BasePort(
            name=self.name,
            kcl=self.kcl,
            cross_section=self.cross_section,
            trans=self.trans.dup() if self.trans else None,
            dcplx_trans=self.dcplx_trans.dup() if self.dcplx_trans else None,
            info=self.info.model_copy(),
            port_type=self.port_type,
        )

    def transformed(
        self,
        trans: kdb.Trans | kdb.DCplxTrans = kdb.Trans.R0,
        post_trans: kdb.Trans | kdb.DCplxTrans = kdb.Trans.R0,
    ) -> BasePort:
        base = self.__copy__()
        if (
            base.trans is not None
            and isinstance(trans, kdb.Trans)
            and isinstance(post_trans, kdb.Trans)
        ):
            base.trans = trans * base.trans * post_trans
            return base
        if isinstance(trans, kdb.Trans):
            trans = kdb.DCplxTrans(trans.to_dtype(self.kcl.dbu))
        if isinstance(post_trans, kdb.Trans):
            post_trans = kdb.DCplxTrans(post_trans.to_dtype(self.kcl.dbu))
        dcplx_trans = self.dcplx_trans or kdb.DCplxTrans(
            t=self.trans.to_dtype(self.kcl.dbu)  # type: ignore[union-attr]
        )

        base.trans = None
        base.dcplx_trans = trans * dcplx_trans * post_trans
        return base

    @model_serializer()
    def ser_model(self) -> BasePortDict:
        if self.trans is not None:
            trans = self.trans.dup()
        else:
            trans = None
        if self.dcplx_trans is not None:
            dcplx_trans = self.dcplx_trans.dup()
        else:
            dcplx_trans = None
        return dict(
            name=self.name,
            kcl=self.kcl,
            cross_section=self.cross_section,
            trans=trans,
            dcplx_trans=dcplx_trans,
            info=self.info.copy(),
            port_type=self.port_type,
        )

    def get_trans(self) -> kdb.Trans:
        return (
            self.trans
            or kdb.ICplxTrans(trans=self.dcplx_trans, dbu=self.kcl.dbu).s_trans()  # type: ignore[arg-type]
        )

    def get_dcplx_trans(self) -> kdb.DCplxTrans:
        return self.dcplx_trans or kdb.DCplxTrans(
            self.trans.to_dtype(self.kcl.dbu)  # type: ignore[union-attr]
        )


class ProtoPort(Generic[TUnit], ABC):
    yaml_tag: str = "!Port"
    _base: BasePort

    def __init__(
        self,
        *,
        name: str | None = None,
        width: TUnit | None = None,
        layer: int | None = None,
        layer_info: kdb.LayerInfo | None = None,
        port_type: str = "optical",
        trans: kdb.Trans | str | None = None,
        dcplx_trans: kdb.DCplxTrans | str | None = None,
        angle: TUnit | None = None,
        center: tuple[TUnit, TUnit] | None = None,
        mirror_x: bool = False,
        port: Port | None = None,
        kcl: KCLayout | None = None,
        info: dict[str, int | float | str] = {},
        cross_section: SymmetricalCrossSection | None = None,
    ) -> None: ...

    @property
    def base(self) -> BasePort:
        return self._base

    @property
    def kcl(self) -> KCLayout:
        """KCLayout associated to the prot."""
        return self._base.kcl

    @kcl.setter
    def kcl(self, value: KCLayout) -> None:
        self._base.kcl = value

    @property
    def cross_section(self) -> SymmetricalCrossSection:
        """CrossSection associated to the prot."""
        return self._base.cross_section

    @cross_section.setter
    def cross_section(self, value: SymmetricalCrossSection) -> None:
        self._base.cross_section = value

    @property
    def name(self) -> str | None:
        """Name of the port."""
        return self._base.name

    @name.setter
    def name(self, value: str | None) -> None:
        self._base.name = value

    @property
    def port_type(self) -> str:
        """Type of the port.

        Usually "optical" or "electrical".
        """
        return self._base.port_type

    @port_type.setter
    def port_type(self, value: str) -> None:
        self._base.port_type = value

    @property
    def info(self) -> Info:
        """Additional info about the port."""
        return self._base.info

    @info.setter
    def info(self, value: Info) -> None:
        self._base.info = value

    @property
    def layer(self) -> LayerEnum | int:
        """Get the layer index of the port.

        This corresponds to the port's cross section's main layer converted to the
        index.
        """
        return self.kcl.find_layer(
            self.cross_section.main_layer, allow_undefined_layers=True
        )

    @property
    def layer_info(self) -> kdb.LayerInfo:
        """Get the layer info of the port.

        This corresponds to the port's cross section's main layer.
        """
        return self.cross_section.main_layer

    def __eq__(self, other: object) -> bool:
        """Support for `port1 == port2` comparisons."""
        if isinstance(other, Port):
            return self._base == other._base
        return False

    @property
    def center(self) -> tuple[TUnit, TUnit]:
        """Returns port center."""
        return (self.x, self.y)

    @center.setter
    def center(self, value: tuple[TUnit, TUnit]) -> None:
        self.x = value[0]
        self.y = value[1]

    @property
    def trans(self) -> kdb.Trans:
        """Simple Transformation of the Port.

        If this is set with the setter, it will overwrite any transformation or
        dcplx transformation
        """
        return (
            self._base.trans
            or kdb.ICplxTrans(self._base.dcplx_trans, self.kcl.layout.dbu).s_trans()
        )

    @trans.setter
    def trans(self, value: kdb.Trans) -> None:
        self._base.trans = value.dup()

    @property
    def dcplx_trans(self) -> kdb.DCplxTrans:
        """Complex transformation (um based).

        If the internal transformation is simple, return a complex copy.

        The setter will set a complex transformation and overwrite the internal
        transformation (set simple to `None` and the complex to the provided value.
        """
        return self._base.dcplx_trans or kdb.DCplxTrans(
            self.trans.to_dtype(self.kcl.layout.dbu)
        )

    @dcplx_trans.setter
    def dcplx_trans(self, value: kdb.DCplxTrans) -> None:
        if value.is_complex() or value.disp != self.kcl.to_um(
            self.kcl.to_dbu(value.disp)
        ):
            self._base.dcplx_trans = value.dup()
        else:
            self._base.trans = kdb.ICplxTrans(value.dup(), self.kcl.dbu).s_trans()

    def to_port(self) -> Port:
        """Convert the port to a regular port."""
        return Port(base=self._base)

    @property
    @abstractmethod
    def x(self) -> TUnit: ...

    @x.setter
    @abstractmethod
    def x(self, value: TUnit) -> None: ...

    @property
    @abstractmethod
    def y(self) -> TUnit: ...

    @y.setter
    @abstractmethod
    def y(self, value: TUnit) -> None: ...

    @property
    @abstractmethod
    def angle(self) -> TUnit: ...

    @angle.setter
    @abstractmethod
    def angle(self, value: int) -> None: ...

    @property
    def orientation(self) -> float:
        """Returns orientation in degrees for gdsfactory compatibility."""
        return self.dcplx_trans.angle

    @orientation.setter
    def orientation(self, value: float) -> None:
        if not self.dcplx_trans.is_complex() and value in [0, 90, 180, 270]:
            self.trans.angle = int(value / 90)
        else:
            self._base.dcplx_trans = self.dcplx_trans
            self._base.dcplx_trans.angle = value

    @property
    @abstractmethod
    def width(self) -> TUnit: ...

    @property
    def mirror(self) -> bool:
        """Returns `True`/`False` depending on the mirror flag on the transformation."""
        return self.trans.is_mirror()

    @mirror.setter
    def mirror(self, value: bool) -> None:
        """Setter for mirror flag on trans."""
        if self._base.trans:
            self._base.trans.mirror = value
        else:
            self._base.dcplx_trans.mirror = value  # type: ignore[union-attr]

    @abstractmethod
    def copy(
        self,
        trans: kdb.Trans | kdb.DCplxTrans = kdb.Trans.R0,
        post_trans: kdb.Trans | kdb.DCplxTrans = kdb.Trans.R0,
    ) -> ProtoPort[TUnit]: ...

    @abstractmethod
    def copy_polar(
        self,
        d: TUnit,
        d_orth: TUnit,
        angle: TUnit,
        mirror: bool = False,
    ) -> ProtoPort[TUnit]: ...

    @property
    def dx(self) -> float:
        """X coordinate of the port in um."""
        return self.dcplx_trans.disp.x

    @dx.setter
    def dx(self, value: float) -> None:
        vec = self.dcplx_trans.disp
        vec.x = value
        if self._base.trans:
            self._base.trans.disp = self.kcl.to_dbu(vec)
        elif self._base.dcplx_trans:
            self._base.dcplx_trans.disp = vec

    @property
    def dy(self) -> float:
        """Y coordinate of the port in um."""
        return self.dcplx_trans.disp.y

    @dy.setter
    def dy(self, value: float) -> None:
        vec = self.dcplx_trans.disp
        vec.y = value
        if self._base.trans:
            self._base.trans.disp = self.kcl.to_dbu(vec)
        elif self._base.dcplx_trans:
            self._base.dcplx_trans.disp = vec

    @property
    def dcenter(self) -> tuple[float, float]:
        """Coordinate of the port in um."""
        vec = self.dcplx_trans.disp
        return (vec.x, vec.y)

    @dcenter.setter
    def dcenter(self, pos: tuple[float, float]) -> None:
        if self._base.trans:
            self._base.trans.disp = self.kcl.to_dbu(kdb.DVector(*pos))
        elif self._base.dcplx_trans:
            self._base.dcplx_trans.disp = kdb.DVector(*pos)

    @property
    def dangle(self) -> float:
        """Angle of the port in degrees."""
        return self.dcplx_trans.angle

    @dangle.setter
    def dangle(self, value: float) -> None:
        if value in [0, 90, 180, 270] and self._base.trans:
            self._base.trans.angle = round(value / 90)
            return

        trans = self.dcplx_trans
        trans.angle = value
        self.dcplx_trans = trans

    @property
    def dwidth(self) -> float:
        """Width of the port in um."""
        return self.kcl.to_um(self._base.cross_section.width)

    @property
    def dmirror(self) -> bool:
        """Mirror flag of the port."""
        return self.mirror

    @dmirror.setter
    def dmirror(self, value: bool) -> None:
        self.mirror = value

    @classmethod
    def from_yaml(cls, constructor: BaseConstructor, node: Any) -> Self:
        """Internal function used by the placer to convert yaml to a Port."""
        d = dict(constructor.construct_pairs(node))
        return cls(**d)


class Port(ProtoPort[int]):
    """A port is the photonics equivalent to a pin in electronics.

    In addition to the location and layer
    that defines a pin, a port also contains an orientation and a width.
    This can be fully represented with a transformation, integer and layer_index.


    Attributes:
        name: String to name the port.
        width: The width of the port in dbu.
        trans: Transformation in dbu. If the port can be represented in 90° intervals
            this is the safe way to do so.
        dcplx_trans: Transformation in micrometer. The port will autoconvert between
            trans and dcplx_trans on demand.
        port_type: A string defining the type of the port
        layer: Index of the layer or a LayerEnum that acts like an integer, but can
            contain layer number and datatype
        info: A dictionary with additional info. Not reflected in GDS. Copy will make a
            (shallow) copy of it.
        d: Access port info in micrometer basis such as width and center / angle.
        kcl: Link to the layout this port resides in.
    """

    @overload
    def __init__(
        self,
        *,
        name: str | None = None,
        width: int,
        layer: LayerEnum | int,
        trans: kdb.Trans,
        kcl: KCLayout | None = None,
        port_type: str = "optical",
        info: dict[str, int | float | str] = {},
    ) -> None: ...

    @overload
    def __init__(
        self,
        *,
        name: str | None = None,
        width: int,
        layer: LayerEnum | int,
        dcplx_trans: kdb.DCplxTrans,
        kcl: KCLayout | None = None,
        port_type: str = "optical",
        info: dict[str, int | float | str] = {},
    ) -> None: ...

    @overload
    def __init__(
        self,
        *,
        name: str | None = None,
        width: int,
        layer: LayerEnum | int,
        port_type: str = "optical",
        angle: int,
        center: tuple[int, int],
        mirror_x: bool = False,
        kcl: KCLayout | None = None,
        info: dict[str, int | float | str] = {},
    ) -> None: ...

    @overload
    def __init__(
        self,
        *,
        name: str | None = None,
        width: int,
        layer_info: kdb.LayerInfo,
        trans: kdb.Trans,
        kcl: KCLayout | None = None,
        port_type: str = "optical",
        info: dict[str, int | float | str] = {},
    ) -> None: ...

    @overload
    def __init__(
        self,
        *,
        name: str | None = None,
        width: int,
        layer_info: kdb.LayerInfo,
        dcplx_trans: kdb.DCplxTrans,
        kcl: KCLayout | None = None,
        port_type: str = "optical",
        info: dict[str, int | float | str] = {},
    ) -> None: ...

    @overload
    def __init__(
        self,
        *,
        name: str | None = None,
        width: int,
        layer_info: kdb.LayerInfo,
        port_type: str = "optical",
        angle: int,
        center: tuple[int, int],
        mirror_x: bool = False,
        kcl: KCLayout | None = None,
        info: dict[str, int | float | str] = {},
    ) -> None: ...

    @overload
    def __init__(
        self,
        *,
        name: str | None = None,
        cross_section: SymmetricalCrossSection,
        port_type: str = "optical",
        angle: int,
        center: tuple[int, int],
        mirror_x: bool = False,
        kcl: KCLayout | None = None,
        info: dict[str, int | float | str] = {},
    ) -> None: ...

    @overload
    def __init__(
        self,
        *,
        name: str | None = None,
        cross_section: SymmetricalCrossSection,
        trans: kdb.Trans,
        kcl: KCLayout | None = None,
        info: dict[str, int | float | str] = {},
        port_type: str = "optical",
    ) -> None: ...

    @overload
    def __init__(
        self,
        *,
        name: str | None = None,
        cross_section: SymmetricalCrossSection,
        dcplx_trans: kdb.DCplxTrans,
        kcl: KCLayout | None = None,
        info: dict[str, int | float | str] = {},
        port_type: str = "optical",
    ) -> None: ...

    @overload
    def __init__(
        self,
        *,
        base: BasePort,
    ) -> None: ...

    def __init__(
        self,
        *,
        name: str | None = None,
        width: int | None = None,
        layer: int | None = None,
        layer_info: kdb.LayerInfo | None = None,
        port_type: str = "optical",
        trans: kdb.Trans | str | None = None,
        dcplx_trans: kdb.DCplxTrans | str | None = None,
        angle: int | None = None,
        center: tuple[int, int] | None = None,
        mirror_x: bool = False,
        port: Port | None = None,
        kcl: KCLayout | None = None,
        info: dict[str, int | float | str] = {},
        cross_section: SymmetricalCrossSection | None = None,
        base: BasePort | None = None,
    ) -> None:
        """Create a port from dbu or um based units."""
        if base is not None:
            self._base = base
            return
        if port is not None:
            self._base = BasePort(**port.base.model_dump())
            return
        info_ = Info(**info)
        from .layout import get_default_kcl

        kcl_ = kcl or get_default_kcl()
        if cross_section is None:
            if layer_info is None:
                if layer is None:
                    raise ValueError("layer or layer_info for a port must be defined")
                layer_info = kcl_.layout.get_info(layer)
            if width is None:
                raise ValueError(
                    "any width and layer, or a cross_section must be given if the"
                    " 'port is None'"
                )
            else:
                cross_section = kcl_.get_cross_section(
                    CrossSectionSpec(main_layer=layer_info, width=width)
                )
        cross_section_ = cross_section
        if trans is not None:
            if isinstance(trans, str):
                trans_ = kdb.Trans.from_s(trans)
            else:
                trans_ = trans.dup()
            self._base = BasePort(
                name=name,
                kcl=kcl_,
                cross_section=cross_section_,
                trans=trans_,
                info=info_,
                port_type=port_type,
            )
        elif dcplx_trans is not None:
            if isinstance(dcplx_trans, str):
                dcplx_trans_ = kdb.DCplxTrans.from_s(dcplx_trans)
            else:
                dcplx_trans_ = dcplx_trans.dup()
            self._base = BasePort(
                name=name,
                kcl=kcl_,
                cross_section=cross_section_,
                dcplx_trans=dcplx_trans_,
                info=info_,
                port_type=port_type,
            )
        elif angle is not None:
            assert center is not None
            trans_ = kdb.Trans(angle, mirror_x, *center)
            self._base = BasePort(
                name=name,
                kcl=kcl_,
                cross_section=cross_section_,
                trans=trans_,
                info=info_,
                port_type=port_type,
            )
        else:
            raise ValueError("Missing port parameters given")

    @property
    def width(self) -> int:
        """Width of the port. This corresponds to the width of the cross section."""
        return self.cross_section.width

    def copy(
        self,
        trans: kdb.Trans | kdb.DCplxTrans = kdb.Trans.R0,
        post_trans: kdb.Trans | kdb.DCplxTrans = kdb.Trans.R0,
    ) -> Port:
        """Get a copy of a port.

        Transformation order which results in `copy.trans`:
            - Trans: `trans * port.trans * post_trans`
            - DCplxTrans: `trans * port.dcplx_trans * post_trans`

        Args:
            trans: an optional transformation applied to the port to be copied.
            post_trans: transformation to apply to the port after copying.

        Returns:
            port: a copy of the port
        """
        return Port(base=self._base.transformed(trans=trans, post_trans=post_trans))

    def copy_polar(
        self, d: int = 0, d_orth: int = 0, angle: int = 2, mirror: bool = False
    ) -> Port:
        """Get a polar copy of the port.

        This will return a port which is transformed relatively to the original port's
        transformation (orientation, angle and position).

        Args:
            d: The distance to the old port
            d_orth: Orthogonal distance (positive is positive y for a port which is
                facing angle=0°)
            angle: Relative angle to the original port (0=0°,1=90°,2=180°,3=270°).
            mirror: Whether to mirror the port relative to the original port.
        """
        return self.copy(post_trans=kdb.Trans(angle, mirror, d, d_orth))

    @property
    def x(self) -> int:
        """X coordinate of the port in dbu."""
        return self.trans.disp.x

    @x.setter
    def x(self, value: int) -> None:
        if self._base.trans:
            vec = self._base.trans.disp
            vec.x = value
            self._base.trans.disp = vec
        elif self._base.dcplx_trans:
            vec = self.trans.disp
            vec.x = value
            self._base.dcplx_trans.disp = self.kcl.to_um(vec)

    @property
    def y(self) -> int:
        """Y coordinate of the port in dbu."""
        return self.trans.disp.y

    @y.setter
    def y(self, value: int) -> None:
        if self._base.trans:
            vec = self._base.trans.disp
            vec.y = value
            self._base.trans.disp = vec
        elif self._base.dcplx_trans:
            vec = self.trans.disp
            vec.y = value
            self._base.dcplx_trans.disp = self.kcl.to_um(vec)

    @property
    def angle(self) -> int:
        """Angle of the transformation.

        In the range of `[0,1,2,3]` which are increments in 90°. Not to be confused
        with `rot` of the transformation which keeps additional info about the
        mirror flag.
        """
        return self.trans.angle

    @angle.setter
    def angle(self, value: int) -> None:
        self._base.trans = self.trans.dup()
        self._base.dcplx_trans = None
        self._base.trans.angle = value

    @property
    def orientation(self) -> float:
        """Returns orientation in degrees for gdsfactory compatibility."""
        return self.dcplx_trans.angle

    @orientation.setter
    def orientation(self, value: float) -> None:
        if not self.dcplx_trans.is_complex() and value in [0, 90, 180, 270]:
            self.trans.angle = int(value / 90)
        else:
            self._base.dcplx_trans = self.dcplx_trans
            self._base.dcplx_trans.angle = value

    @property
    def mirror(self) -> bool:
        """Returns `True`/`False` depending on the mirror flag on the transformation."""
        return self.trans.is_mirror()

    @mirror.setter
    def mirror(self, value: bool) -> None:
        """Setter for mirror flag on trans."""
        if self._base.trans:
            self._base.trans.mirror = value
        else:
            self._base.dcplx_trans.mirror = value  # type: ignore[union-attr]

    def __repr__(self) -> str:
        """String representation of port."""
        return (
            f"Port({'name: ' + self.name if self.name else ''}"
            f", width: {self.width}, trans: {self.dcplx_trans.to_s()}, layer: "
            f"{self.layer_info}, port_type: {self.port_type})"
        )

    def print(self, type: Literal["dbu", "um", None] = None) -> None:
        """Print the port pretty."""
        config.console.print(pprint_ports([self], unit=type))


class DPort(ProtoPort[float]):
    def __init__(
        self,
        *,
        name: str | None = None,
        width: float | None = None,
        layer: int | None = None,
        layer_info: kdb.LayerInfo | None = None,
        port_type: str = "optical",
        trans: kdb.Trans | str | None = None,
        dcplx_trans: kdb.DCplxTrans | str | None = None,
        angle: float | None = None,
        center: tuple[float, float] | None = None,
        mirror_x: bool = False,
        port: Port | DPort | None = None,
        kcl: KCLayout | None = None,
        info: dict[str, int | float | str] = {},
        cross_section: SymmetricalCrossSection | None = None,
        base: BasePort | None = None,
    ) -> None:
        """Create a port from dbu or um based units."""
        if base is not None:
            self._base = base
            return
        if port is not None:
            self._base = BasePort(**port.base.model_dump())
            return
        info_ = Info(**info)

        from .layout import get_default_kcl

        kcl_ = kcl or get_default_kcl()
        if cross_section is None:
            if layer_info is None:
                if layer is None:
                    raise ValueError("layer or layer_info for a port must be defined")
                layer_info = kcl_.layout.get_info(layer)
            if width is None:
                raise ValueError(
                    "If a cross_section is not given a width must be defined."
                )
            cross_section = kcl_.get_cross_section(
                CrossSectionSpec(main_layer=layer_info, width=width)
            )
        cross_section_ = cross_section
        if trans is not None:
            if isinstance(trans, str):
                trans_ = kdb.Trans.from_s(trans)
            else:
                trans_ = trans.dup()
            self._base = BasePort(
                name=name,
                kcl=kcl_,
                cross_section=cross_section_,
                trans=trans_,
                info=info_,
                port_type=port_type,
            )
        elif dcplx_trans is not None:
            if isinstance(dcplx_trans, str):
                dcplx_trans_ = kdb.DCplxTrans.from_s(dcplx_trans)
            else:
                dcplx_trans_ = dcplx_trans.dup()
            self._base = BasePort(
                name=name,
                kcl=kcl_,
                cross_section=cross_section_,
                dcplx_trans=dcplx_trans_,
                info=info_,
                port_type=port_type,
            )
        elif angle is not None:
            assert center is not None
            dcplx_trans_ = kdb.DCplxTrans.R0
            self._base = BasePort(
                name=name,
                kcl=kcl_,
                cross_section=cross_section_,
                dcplx_trans=dcplx_trans_,
                info=info_,
                port_type=port_type,
            )
            self.center = center
            self.angle = angle
        else:
            raise ValueError("Missing port parameters given")

    def __repr__(self) -> str:
        """String representation of port."""
        return (
            f"DPort({'name: ' + self.name if self.name else ''}"
            f", width: {self.width}, trans: {self.dcplx_trans.to_s()}, layer: "
            f"{self.layer_info}, port_type: {self.port_type})"
        )

    def copy(
        self,
        trans: kdb.Trans | kdb.DCplxTrans = kdb.Trans.R0,
        post_trans: kdb.Trans | kdb.DCplxTrans = kdb.Trans.R0,
    ) -> DPort:
        """Get a copy of a port.

        Transformation order which results in `copy.trans`:
            - Trans: `trans * port.trans * post_trans`
            - DCplxTrans: `trans * port.dcplx_trans * post_trans`

        Args:
            trans: an optional transformation applied to the port to be copied.
            post_trans: transformation to apply to the port after copying.

        Returns:
            port: a copy of the port
        """
        return DPort(base=self._base.transformed(trans=trans, post_trans=post_trans))

    def copy_polar(
        self, d: float = 0, d_orth: float = 0, angle: float = 2, mirror: bool = False
    ) -> DPort:
        """Get a polar copy of the port.

        This will return a port which is transformed relatively to the original port's
        transformation (orientation, angle and position).

        Args:
            d: The distance to the old port
            d_orth: Orthogonal distance (positive is positive y for a port which is
                facing angle=0°)
            angle: Relative angle to the original port (0=0°,1=90°,2=180°,3=270°).
            mirror: Whether to mirror the port relative to the original port.
        """
        return self.copy(
            post_trans=kdb.DCplxTrans(rot=angle, mirrx=mirror, x=d, y=d_orth)
        )

    @property
    def x(self) -> float:
        """X coordinate of the port in um."""
        return self.dcplx_trans.disp.x

    @x.setter
    def x(self, value: float) -> None:
        vec = self.dcplx_trans.disp
        vec.x = value
        if self._base.trans:
            self._base.trans.disp = self.kcl.to_dbu(vec)
        elif self._base.dcplx_trans:
            self._base.dcplx_trans.disp = vec

    @property
    def y(self) -> float:
        """Y coordinate of the port in um."""
        return self.dcplx_trans.disp.y

    @y.setter
    def y(self, value: float) -> None:
        vec = self.dcplx_trans.disp
        vec.y = value
        if self._base.trans:
            self._base.trans.disp = self.kcl.to_dbu(vec)
        elif self._base.dcplx_trans:
            self._base.dcplx_trans.disp = vec

    @property
    def center(self) -> tuple[float, float]:
        """Coordinate of the port in um."""
        vec = self.dcplx_trans.disp
        return (vec.x, vec.y)

    @center.setter
    def center(self, value: tuple[float, float]) -> None:
        if self._base.trans:
            self._base.trans.disp = self.kcl.to_dbu(kdb.DVector(*value))
        elif self._base.dcplx_trans:
            self._base.dcplx_trans.disp = kdb.DVector(*value)

    @property
    def angle(self) -> float:
        """Angle of the port in degrees."""
        return self.dcplx_trans.angle

    @angle.setter
    def angle(self, value: float) -> None:
        if value in [0, 90, 180, 270] and self._base.trans:
            self._base.trans.angle = round(value / 90)
            return

        trans = self.dcplx_trans
        trans.angle = value
        self.dcplx_trans = trans

    @property
    def width(self) -> float:
        """Width of the port in um."""
        return self.kcl.to_um(self._base.cross_section.width)

    @property
    def mirror(self) -> bool:
        """Mirror flag of the port."""
        return self.mirror

    @mirror.setter
    def mirror(self, value: bool) -> None:
        self.mirror = value


class DIRECTION(IntEnum):
    """Alias for KLayout direction to compass directions."""

    E = 0
    N = 1
    W = 2
    S = 3


def autorename(
    c: KCell,
    f: Callable[..., None],
    *args: Any,
    **kwargs: Any,
) -> None:
    """Rename a KCell with a renaming function.

    Args:
        c: KCell to be renamed.
        f: Renaming function.
        args: Arguments for the renaming function.
        kwargs: Keyword arguments for the renaming function.
    """
    f(c.ports, *args, **kwargs)


def rename_clockwise(
    ports: Iterable[ProtoPort[Any]],
    layer: LayerEnum | int | None = None,
    port_type: str | None = None,
    regex: str | None = None,
    prefix: str = "o",
    start: int = 1,
) -> None:
    """Sort and return ports in the clockwise direction.

    Args:
        ports: List of ports to rename.
        layer: Layer index / LayerEnum of port layer.
        port_type: Port type to filter the ports by.
        regex: Regex string to filter the port names by.
        prefix: Prefix to add to all ports.
        start: Start index per orientation.

    ```
             o3  o4
             |___|_
        o2 -|      |- o5
            |      |
        o1 -|______|- o6
             |   |
            o8  o7
    ```
    """
    ports_ = filter_layer_pt_reg(ports, layer, port_type, regex)

    def sort_key(port: ProtoPort[Any]) -> tuple[int, int, int]:
        match port.trans.angle:
            case 2:
                angle = 0
            case 1:
                angle = 1
            case 0:
                angle = 2
            case 3:
                angle = 3
            case _:
                raise ValueError(f"Invalid angle: {port.angle}")
        dir_1 = 1 if angle < 2 else -1
        dir_2 = -1 if port.angle < 2 else 1
        key_1 = dir_1 * (
            port.trans.disp.x if angle % 2 else port.trans.disp.y
        )  # order should be y, x, -y, -x
        key_2 = dir_2 * (
            port.trans.disp.y if angle % 2 else port.trans.disp.x
        )  # order should be x, -y, -x, y

        return angle, key_1, key_2

    for i, p in enumerate(sorted(ports_, key=sort_key), start=start):
        p.name = f"{prefix}{i}"


def rename_clockwise_multi(
    ports: Iterable[ProtoPort[Any]],
    layers: Iterable[LayerEnum | int] | None = None,
    regex: str | None = None,
    type_prefix_mapping: dict[str, str] = {"optical": "o", "electrical": "e"},
    start: int = 1,
) -> None:
    """Sort and return ports in the clockwise direction.

    Args:
        ports: List of ports to rename.
        layers: Layer indexes / LayerEnums of port layers to rename.
        type_prefix_mapping: Port type to prefix matching in a dictionary.
        regex: Regex string to filter the port names by.
        start: Start index per orientation.

    ```
             o3  o4
             |___|_
        o2 -|      |- o5
            |      |
        o1 -|______|- o6
             |   |
            o8  o7
    ```
    """
    if layers:
        for p_type, prefix in type_prefix_mapping.items():
            for layer in layers:
                rename_clockwise(
                    ports=ports,
                    layer=layer,
                    port_type=p_type,
                    regex=regex,
                    prefix=prefix,
                    start=start,
                )
    else:
        for p_type, prefix in type_prefix_mapping.items():
            rename_clockwise(
                ports=ports,
                layer=None,
                port_type=p_type,
                regex=regex,
                prefix=prefix,
                start=start,
            )


def rename_by_direction(
    ports: Iterable[ProtoPort[Any]],
    layer: LayerEnum | int | None = None,
    port_type: str | None = None,
    regex: str | None = None,
    dir_names: tuple[str, str, str, str] = ("E", "N", "W", "S"),
    prefix: str = "",
) -> None:
    """Rename ports by angle of their transformation.

    Args:
        ports: list of ports to be renamed
        layer: A layer index to filter by
        port_type: port_type string to filter by
        regex: Regex string to use to filter the ports to be renamed.
        dir_names: Prefixes for the directions (east, north, west, south).
        prefix: Prefix to add before `dir_names`

    ```
             N0  N1
             |___|_
        W1 -|      |- E1
            |      |
        W0 -|______|- E0
             |   |
            S0   S1
    ```
    """
    for dir in DIRECTION:
        ports_ = filter_layer_pt_reg(ports, layer, port_type, regex)
        dir_2 = -1 if dir < 2 else 1
        if dir % 2:

            def key_sort(port: ProtoPort[Any]) -> tuple[int, int]:
                return (port.trans.disp.x, dir_2 * port.trans.disp.y)

        else:

            def key_sort(port: ProtoPort[Any]) -> tuple[int, int]:
                return (port.trans.disp.y, dir_2 * port.trans.disp.x)

        for i, p in enumerate(sorted(filter_direction(ports_, dir), key=key_sort)):
            p.name = f"{prefix}{dir_names[dir]}{i}"


def filter_layer_pt_reg(
    ports: Iterable[TPort],
    layer: LayerEnum | int | None = None,
    port_type: str | None = None,
    regex: str | None = None,
) -> Iterable[TPort]:
    """Filter ports by layer index, port type and name regex."""
    ports_ = ports
    if layer is not None:
        ports_ = filter_layer(ports_, layer)
    if port_type is not None:
        ports_ = filter_port_type(ports_, port_type)
    if regex is not None:
        ports_ = filter_regex(ports_, regex)

    return ports_


def filter_direction(ports: Iterable[TPort], direction: int) -> filter[TPort]:
    """Filter iterable/sequence of ports by direction :py:class:~`DIRECTION`."""

    def f_func(p: TPort) -> bool:
        return p.trans.angle == direction

    return filter(f_func, ports)


def filter_orientation(ports: Iterable[TPort], orientation: float) -> filter[TPort]:
    """Filter iterable/sequence of ports by direction :py:class:~`DIRECTION`."""

    def f_func(p: TPort) -> bool:
        return p.dcplx_trans.angle == orientation

    return filter(f_func, ports)


def filter_port_type(ports: Iterable[TPort], port_type: str) -> filter[TPort]:
    """Filter iterable/sequence of ports by port_type."""

    def pt_filter(p: TPort) -> bool:
        return p.port_type == port_type

    return filter(pt_filter, ports)


def filter_layer(ports: Iterable[TPort], layer: int | LayerEnum) -> filter[TPort]:
    """Filter iterable/sequence of ports by layer index / LayerEnum."""

    def layer_filter(p: TPort) -> bool:
        return p.layer == layer

    return filter(layer_filter, ports)


def filter_regex(ports: Iterable[TPort], regex: str) -> filter[TPort]:
    """Filter iterable/sequence of ports by port name."""
    pattern = re.compile(regex)

    def regex_filter(p: TPort) -> bool:
        if p.name is not None:
            return bool(pattern.match(p.name))
        else:
            return False

    return filter(regex_filter, ports)


polygon_dict: dict[int, kdb.Polygon] = {}


def port_polygon(width: int) -> kdb.Polygon:
    """Gets a polygon representation for a given port width."""
    if width in polygon_dict:
        return polygon_dict[width]
    else:
        poly = kdb.Polygon(
            [
                kdb.Point(0, width // 2),
                kdb.Point(0, -width // 2),
                kdb.Point(width // 2, 0),
            ]
        )

        hole = kdb.Region(poly).sized(-int(width * 0.05) or -1)
        hole -= kdb.Region(kdb.Box(0, 0, width // 2, -width // 2))

        poly.insert_hole(list(next(iter(hole.each())).each_point_hull()))
        polygon_dict[width] = poly
        return poly
