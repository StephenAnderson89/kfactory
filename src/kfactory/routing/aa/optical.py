"""Optical routing allows the creation of photonic (or any route using bends)."""

from collections.abc import Callable, Sequence
from typing import Protocol

import numpy as np

# from typing import Any
from pydantic import BaseModel

from ... import kdb
from ...conf import config
from ...kcell import Port, VInstance, VKCell

__all__ = ["OpticalAllAngleRoute", "route"]


class OpticalAllAngleRoute(BaseModel, arbitrary_types_allowed=True):
    """Optical route containing a connection between two ports."""

    backbone: list[kdb.DPoint]
    start_port: Port
    end_port: Port
    instances: list[VInstance]


def _angle(v: kdb.DVector) -> float:
    return float(np.rad2deg(np.arctan2(v.y, v.x)))


@config.logger.catch(reraise=True)
def route(
    c: VKCell,
    width: float,
    layer: int,
    backbone: Sequence[kdb.DPoint],
    straight_factory: Callable[[float, float], VKCell],
    bend_factory: Callable[[float, float], VKCell],
    bend_ports: tuple[str, str] = ("o1", "o2"),
    straight_ports: tuple[str, str] = ("o1", "o2"),
) -> OpticalAllAngleRoute:
    """Places a route."""
    if len(backbone) < 3:
        raise ValueError("All angle routes with less than 3 points are not supported.")

    bends: dict[float, VKCell] = {}

    start_v = backbone[1] - backbone[0]
    end_v = backbone[-1] - backbone[-2]
    start_angle = np.rad2deg(np.arctan2(start_v.y, start_v.x))
    end_angle = (np.rad2deg(np.arctan2(end_v.y, end_v.x)) + 180) % 360

    start_port = Port(
        name="o1",
        dwidth=width,
        layer=layer,
        dcplx_trans=kdb.DCplxTrans(1, start_angle, False, backbone[0].to_v()),
        kcl=c.kcl,
    )
    end_port = Port(
        name="o1",
        dwidth=width,
        layer=layer,
        dcplx_trans=kdb.DCplxTrans(1, end_angle, False, backbone[-1].to_v()),
        kcl=c.kcl,
    )

    old_pt = backbone[0]
    pt = backbone[1]
    start_offset = 0.0
    _port = start_port
    insts: list[VInstance] = []

    for new_pt in backbone[2:]:
        # Calculate (4 quadrant) angle between the three points
        s_v = pt - old_pt
        e_v = new_pt - pt
        s_a = _angle(s_v)
        e_a = _angle(e_v)
        _a = (e_a - s_a + 180) % 360 - 180

        if _a != 0:
            # create a virtual bend with the angle if non-existent
            if _a not in bends:
                bends[_a] = bend_factory(width=width, angle=abs(_a))  # type: ignore[call-arg]
            bend = bends[_a]

            p1, p2 = (bend.ports[_p] for _p in bend_ports)

            # get the center of the bend
            # the center must be on the crossing point between the two
            v = kdb.DVector(1, 0)
            dt1 = p1.dcplx_trans
            dt2 = p2.dcplx_trans
            dp11 = dt1.disp.to_p()
            dp21 = dt2.disp.to_p()
            dp12 = dp11 + dt1 * v
            dp22 = dp21 + dt2 * v

            e1 = kdb.DEdge(dp11, dp12)
            e2 = kdb.DEdge(dp21, dp22)
            cp = e1.cut_point(e2)

            # from this the effective radius can be calculated (the bend must be
            # symmetric so each lengths needs 1*eff_radius)
            effective_radius = (cp - p1.dcplx_trans.disp.to_p()).length()

            # if the resulting straight is < old_eff_radius + new_eff_radius
            # the route is invalid
            if (pt - old_pt).length() - effective_radius - start_offset < 0:
                raise ValueError(
                    f"Not enough space to place bends at points {[old_pt, pt]}."
                    f"Needed space={start_offset + effective_radius}, available "
                    f"space={(pt - old_pt).length()}"
                )
        else:
            effective_radius = 0

        # calculate and place the resulting straight if != 0
        _l = (pt - old_pt).length() - effective_radius - start_offset
        if _l > 0:
            s = c << straight_factory(width=width, length=_l)  # type:ignore[call-arg]
            s.connect(straight_ports[0], _port)
            _port = s.ports[straight_ports[1]]
            insts.append(s)
        if _a != 0:
            # after the straight place the bend
            b = c << bend
            if _a < 0:
                b.connect(bend_ports[1], _port)
                _port = b.ports[bend_ports[0]]
            else:
                b.connect(bend_ports[0], _port)
                _port = b.ports[bend_ports[1]]
            insts.append(b)
        start_offset = effective_radius
        old_pt = pt
        pt = new_pt
    # place last straight
    _l = (pt - old_pt).length() - effective_radius
    # if the resulting straight is < old_eff_radius + new_eff_radius
    # the route is invalid
    if _l < 0:
        raise ValueError(
            f"Not enough space to place bends at points {[old_pt, pt]}."
            f"Needed space={effective_radius}, available "
            f"space={(pt - old_pt).length()}"
        )
    if _l > 0:
        s = c << straight_factory(width=width, length=_l)  # type:ignore[call-arg]
        s.connect(straight_ports[0], _port)
        _port = s.ports[straight_ports[1]]
        insts.append(s)

    route = OpticalAllAngleRoute(
        backbone=backbone, start_port=start_port, end_port=end_port, instances=insts
    )
    return route


class RadiusEstimate(Protocol):
    def __call__(self, angle: float) -> float: ...


@config.logger.catch(reraise=True)
def route_bundle(
    c: VKCell,
    start_ports: list[Port],
    end_ports: list[Port],
    backbone: Sequence[kdb.DPoint],
    spacings: list[float],
    radius_estimate: RadiusEstimate,
    straight_factory: Callable[[float, float], VKCell],
    bend_factory: Callable[[float, float], VKCell],
    bend_ports: tuple[str, str] = ("o1", "o2"),
    straight_ports: tuple[str, str] = ("o1", "o2"),
) -> list[OpticalAllAngleRoute]:
    pts_list = backbone2bundle(
        backbone=backbone,
        port_widths=[p.d.width for p in start_ports],
        spacings=spacings,
    )

    routes: list[OpticalAllAngleRoute] = []

    for ps, pe, pts in zip(start_ports, end_ports, pts_list):
        # needs the estimation algorithm here, just hardcoding for now
        # TODO: replace with proper algorithm
        p_radius = kdb.DPoint(200, 0)

        _p0 = kdb.DPoint(0, 0)
        _p1 = kdb.DPoint(1, 0)

        # use edges and transformation to get distances to calculate crossings
        # and types of crossings
        vector_bundle_start = pts[0] - pts[1]
        vector_bundle_end = pts[-1] - pts[-2]
        trans_bundle_start = kdb.DCplxTrans(
            1,
            np.rad2deg(np.arctan2(vector_bundle_start.y, vector_bundle_start.x)),
            False,
            pts[0].to_v(),
        )
        trans_bundle_end = kdb.DCplxTrans(
            1,
            np.rad2deg(np.arctan2(vector_bundle_end.y, vector_bundle_end.x)),
            False,
            pts[-1].to_v(),
        )
        edge_start = kdb.DEdge(ps.dcplx_trans * _p0, ps.dcplx_trans * _p1)
        edge_bundle_start = kdb.DEdge(
            trans_bundle_start * _p0, trans_bundle_start * _p1
        )
        xing_start = edge_start.cut_point(edge_bundle_start)

        edge_end = kdb.DEdge(pe.dcplx_trans * _p0, pe.dcplx_trans * _p1)
        edge_bundle_end = kdb.DEdge(trans_bundle_end * _p0, trans_bundle_end * _p1)
        xing_end = edge_end.cut_point(edge_bundle_end)

        if xing_start is not None:
            # if the crossings point to each other use one bend, otherwise use two
            vector_xing_start = ps.dcplx_trans.inverted() * xing_start
            vector_xing_bundle_start = trans_bundle_start.inverted() * xing_start
            if vector_xing_start.x > 0 and vector_xing_bundle_start.x > 0:
                pts[:0] = [ps.dcplx_trans.disp.to_p(), xing_start]
            else:
                pts[:1] = [
                    ps.dcplx_trans.disp.to_p(),
                    ps.dcplx_trans * p_radius,
                    trans_bundle_start * p_radius,
                ]
        else:
            pts[:1] = [
                ps.dcplx_trans.disp.to_p(),
                ps.dcplx_trans * p_radius,
                trans_bundle_start * p_radius,
            ]
        if xing_end is not None:
            vector_xing_end = pe.dcplx_trans.inverted() * xing_end
            vector_xing_bundle_end = trans_bundle_end.inverted() * xing_end
            if vector_xing_end.x > 0 and vector_xing_bundle_end.x > 0:
                pts.extend([xing_end, pe.dcplx_trans.disp.to_p()])
            else:
                pts[-1:] = [
                    trans_bundle_end * p_radius,
                    pe.dcplx_trans * p_radius,
                    pe.dcplx_trans.disp.to_p(),
                ]
        else:
            pts[-1:] = [
                trans_bundle_end * p_radius,
                pe.dcplx_trans * p_radius,
                pe.dcplx_trans.disp.to_p(),
            ]

        routes.append(
            route(
                c,
                ps.d.width,
                ps.layer,
                pts,
                straight_factory=straight_factory,
                bend_factory=bend_factory,
                bend_ports=bend_ports,
                straight_ports=straight_ports,
            )
        )

    return routes


def backbone2bundle(
    backbone: Sequence[kdb.DPoint],
    port_widths: list[float],
    spacings: list[float],
) -> list[list[kdb.DPoint]]:
    """Used to extract a bundle from a backbone."""
    pts: list[list[kdb.DPoint]] = []

    edges: list[kdb.DEdge] = []
    p1 = backbone[0]

    for p2 in backbone[1:]:
        edges.append(kdb.DEdge(p1, p2))
        p1 = p2

    width = sum(port_widths) + sum(spacings)

    x = -width // 2

    for pw, spacing in zip(port_widths, spacings):
        x += pw // 2 + spacing // 2

        _e1 = edges[0].shifted(-x)
        _pts = [_e1.p1]

        for e in edges[1:]:
            _e2 = e.shifted(-x)
            _pts.append(_e2.cut_point(_e1))
            _e1 = _e2
        _pts.append(_e1.p2)

        x += spacing - spacing // 2 + pw - pw // 2
        pts.append(_pts)

    return pts
