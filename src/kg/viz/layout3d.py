from __future__ import annotations

from dataclasses import dataclass
from math import cos, isfinite, pi, sin, sqrt

BH_THETA = 1.2
OCTREE_MAX_DEPTH = 26
OCTREE_MIN_HALF = 1e-4
LOCAL_REPULSION = 8.0
LOCAL_ATTRACTION = 1.0
LOCAL_ANCHOR_K = 0.25
LOCAL_ITERATIONS = 40
MAX_DISPLACEMENT = 8.0


@dataclass(frozen=True, slots=True)
class LayoutNodeInput:
    id: str
    cluster_key: str
    name: str
    label: str
    degree: int


@dataclass(frozen=True, slots=True)
class LayoutEdgeInput:
    source: str
    target: str
    type: str


@dataclass(frozen=True, slots=True)
class PositionedNode:
    render_id: int
    kg_id: str
    x: float
    y: float
    z: float
    size: float


@dataclass(frozen=True, slots=True)
class PositionedEdge:
    source: int
    target: int
    type: str


@dataclass(frozen=True, slots=True)
class LayoutResult:
    nodes: tuple[PositionedNode, ...]
    edges: tuple[PositionedEdge, ...]


def fnv1a_32(value: bytes) -> int:
    result = 0x811C9DC5
    for byte in value:
        result ^= byte
        result = (result * 0x01000193) & 0xFFFFFFFF
    return result


class _Rng:
    __slots__ = ("seed",)

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def next(self) -> float:
        self.seed = (self.seed * 1103515245 + 12345) & 0xFFFFFFFF
        return ((self.seed >> 16) & 0x7FFF) / 32768.0 - 0.5


@dataclass(slots=True)
class _Body:
    x: float
    y: float
    z: float
    ax: float
    ay: float
    az: float
    fx: float
    fy: float
    fz: float
    mass: float


@dataclass(slots=True)
class _Octant:
    ox: float
    oy: float
    oz: float
    half: float
    cx: float = 0.0
    cy: float = 0.0
    cz: float = 0.0
    total_mass: float = 0.0
    body_index: int = -1
    body_mass: float = 0.0
    children: list[_Octant | None] | None = None

    def insert(self, index: int, x: float, y: float, z: float, mass: float, depth: int) -> None:
        if self.total_mass == 0.0 and self.body_index == -1:
            self.body_index = index
            self.body_mass = mass
            self.cx = x
            self.cy = y
            self.cz = z
            self.total_mass = mass
            return
        if depth >= OCTREE_MAX_DEPTH or self.half < OCTREE_MIN_HALF:
            nm = self.total_mass + mass
            self.cx = (self.cx * self.total_mass + x * mass) / nm
            self.cy = (self.cy * self.total_mass + y * mass) / nm
            self.cz = (self.cz * self.total_mass + z * mass) / nm
            self.total_mass = nm
            self.body_index = -1
            return
        if self.body_index >= 0:
            oi = self.body_index
            ox, oy, oz, om = self.cx, self.cy, self.cz, self.body_mass
            self.body_index = -1
            o = self._octant(ox, oy, oz)
            self._child(o).insert(oi, ox, oy, oz, om, depth + 1)
        nm = self.total_mass + mass
        self.cx = (self.cx * self.total_mass + x * mass) / nm
        self.cy = (self.cy * self.total_mass + y * mass) / nm
        self.cz = (self.cz * self.total_mass + z * mass) / nm
        self.total_mass = nm
        self._child(self._octant(x, y, z)).insert(index, x, y, z, mass, depth + 1)

    def _octant(self, x: float, y: float, z: float) -> int:
        return ((1 if x >= self.ox else 0) | (2 if y >= self.oy else 0) | (4 if z >= self.oz else 0))

    def _child(self, o: int) -> _Octant:
        if self.children is None:
            self.children = [None] * 8
        child = self.children[o]
        if child is None:
            q = self.half * 0.5
            child = _Octant(
                self.ox + (q if o & 1 else -q),
                self.oy + (q if o & 2 else -q),
                self.oz + (q if o & 4 else -q),
                self.half * 0.5,
            )
            self.children[o] = child
        return child

    def repulse(self, px: float, py: float, pz: float, mm: float, si: int, kr: float) -> tuple[float, float, float]:
        fx: float = 0.0
        fy: float = 0.0
        fz: float = 0.0
        stack = [self]
        while stack:
            n = stack.pop()
            if n is None or n.total_mass == 0.0 or n.body_index == si:
                continue
            dx = px - n.cx
            dy = py - n.cy
            dz = pz - n.cz
            d = sqrt(dx * dx + dy * dy + dz * dz)
            if n.body_index >= 0 or (n.half * 2.0 / (d + 0.001)) < BH_THETA:
                if d < 0.01:
                    d = 0.01
                f = kr * mm * n.total_mass / d
                fx += f * dx / d
                fy += f * dy / d
                fz += f * dz / d
                continue
            if n.children is not None:
                for child in n.children:
                    stack.append(child)
        return fx, fy, fz


def _local_optimize(bodies: list[_Body], edges: list[tuple[int, int]]) -> None:
    n = len(bodies)
    iterations = LOCAL_ITERATIONS
    if n > 500000:
        iterations = 10
    elif n > 100000:
        iterations = 20
    for _ in range(iterations):
        for body in bodies:
            body.fx = 0.0
            body.fy = 0.0
            body.fz = 0.0
        if n:
            mnx = min(body.x for body in bodies)
            mny = min(body.y for body in bodies)
            mnz = min(body.z for body in bodies)
            mxx = max(body.x for body in bodies)
            mxy = max(body.y for body in bodies)
            mxz = max(body.z for body in bodies)
            half = max(mxx - mnx, mxy - mny, mxz - mnz) * 0.5 + 1.0
            root = _Octant((mnx + mxx) * 0.5, (mny + mxy) * 0.5, (mnz + mxz) * 0.5, half)
            for i, body in enumerate(bodies):
                root.insert(i, body.x, body.y, body.z, body.mass, 0)
            for i, body in enumerate(bodies):
                body.fx, body.fy, body.fz = root.repulse(
                    body.x, body.y, body.z, body.mass, i, LOCAL_REPULSION
                )
        for s, t in edges:
            if s < 0 or s >= n or t < 0 or t >= n:
                continue
            dx = bodies[t].x - bodies[s].x
            dy = bodies[t].y - bodies[s].y
            dz = bodies[t].z - bodies[s].z
            bodies[s].fx += dx * LOCAL_ATTRACTION
            bodies[s].fy += dy * LOCAL_ATTRACTION
            bodies[s].fz += dz * LOCAL_ATTRACTION
            bodies[t].fx -= dx * LOCAL_ATTRACTION
            bodies[t].fy -= dy * LOCAL_ATTRACTION
            bodies[t].fz -= dz * LOCAL_ATTRACTION
        for body in bodies:
            body.fx += (body.ax - body.x) * LOCAL_ANCHOR_K * body.mass
            body.fy += (body.ay - body.y) * LOCAL_ANCHOR_K * body.mass
            body.fz += (body.az - body.z) * LOCAL_ANCHOR_K * body.mass
        for body in bodies:
            fm = sqrt(body.fx * body.fx + body.fy * body.fy + body.fz * body.fz)
            speed = 1.0
            if speed * fm > MAX_DISPLACEMENT:
                speed = MAX_DISPLACEMENT / (fm + 0.001)
            body.x += body.fx * speed
            body.y += body.fy * speed
            body.z += body.fz * speed


def layout_graph(
    nodes: list[LayoutNodeInput],
    edges: list[LayoutEdgeInput],
) -> LayoutResult:
    ordered = sorted(nodes, key=lambda n: n.id)
    render_id = {n.id: i for i, n in enumerate(ordered)}

    bodies: list[_Body] = []
    for node in ordered:
        key_bytes = node.cluster_key.encode("utf-8")
        id_bytes = node.id.encode("utf-8")
        cluster_hash = fnv1a_32(key_bytes)
        angle = (cluster_hash & 0xFFFF) / 65535.0 * 2 * pi
        radius = 500.0 + ((cluster_hash >> 16) & 0xFF) / 255.0 * 250.0
        rng = _Rng(fnv1a_32(id_bytes))
        jitter = 20.0
        x = radius * cos(angle) + rng.next() * jitter
        y = radius * sin(angle) + rng.next() * jitter
        z = rng.next() * jitter
        mass = float(node.degree + 1)
        bodies.append(_Body(x, y, z, x, y, z, 0.0, 0.0, 0.0, mass))

    retained: list[tuple[int, int, str]] = []
    for edge in edges:
        s = render_id.get(edge.source)
        t = render_id.get(edge.target)
        if s is not None and t is not None:
            retained.append((s, t, edge.type))
    retained.sort(key=lambda e: (e[0], e[1], e[2]))

    _local_optimize(bodies, [(s, t) for s, t, _ in retained])

    placed = []
    for i, node in enumerate(ordered):
        x = bodies[i].x if isfinite(bodies[i].x) else 0.0
        y = bodies[i].y if isfinite(bodies[i].y) else 0.0
        z = bodies[i].z if isfinite(bodies[i].z) else 0.0
        size = sqrt(float(node.degree + 1.0))
        size = size if isfinite(size) and size >= 1.0 else 1.0
        placed.append(
            PositionedNode(render_id=i, kg_id=node.id, x=x, y=y, z=z, size=size)
        )

    return LayoutResult(
        nodes=tuple(placed),
        edges=tuple(PositionedEdge(source=s, target=t, type=typ) for s, t, typ in retained),
    )
