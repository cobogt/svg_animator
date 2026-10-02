#!/usr/bin/env python3
"""Convierte un SVG en un AnimatedVectorDrawable de Android (vector + animated-vector + animator XML)."""

import argparse
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from xml.dom import minidom

ANDROID_NS = "http://schemas.android.com/apk/res/android"
AAPT_NS = "http://schemas.android.com/aapt"

NAMED_COLORS = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000",
    "blue": "#0000ff", "yellow": "#ffff00", "gray": "#808080", "grey": "#808080",
    "orange": "#ffa500", "purple": "#800080", "pink": "#ffc0cb", "brown": "#a52a2a",
    "cyan": "#00ffff", "magenta": "#ff00ff", "lime": "#00ff00", "navy": "#000080",
    "teal": "#008080", "maroon": "#800000", "olive": "#808000", "silver": "#c0c0c0",
    "transparent": "#00000000", "none": None,
}

ARC_SEG_MAX_DEG = 90  # tamano maximo de cada tramo de arco al aproximarlo con beziers


# --------------------------------------------------------------------------
# Matriz afin 2D: x' = a*x + c*y + e ; y' = b*x + d*y + f
# --------------------------------------------------------------------------
class Matrix:
    __slots__ = ("a", "b", "c", "d", "e", "f")

    def __init__(self, a=1.0, b=0.0, c=0.0, d=1.0, e=0.0, f=0.0):
        self.a, self.b, self.c, self.d, self.e, self.f = a, b, c, d, e, f

    def multiply(self, other):
        # self aplicada despues de other: resultado = self * other
        return Matrix(
            self.a * other.a + self.c * other.b,
            self.b * other.a + self.d * other.b,
            self.a * other.c + self.c * other.d,
            self.b * other.c + self.d * other.d,
            self.a * other.e + self.c * other.f + self.e,
            self.b * other.e + self.d * other.f + self.f,
        )

    def apply(self, x, y):
        return (self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f)

    @staticmethod
    def translate(tx, ty=0.0):
        return Matrix(1, 0, 0, 1, tx, ty)

    @staticmethod
    def scale(sx, sy=None):
        if sy is None:
            sy = sx
        return Matrix(sx, 0, 0, sy, 0, 0)

    @staticmethod
    def rotate(deg, cx=0.0, cy=0.0):
        rad = math.radians(deg)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        m = Matrix(cos_a, sin_a, -sin_a, cos_a, 0, 0)
        if cx or cy:
            m = Matrix.translate(cx, cy).multiply(m).multiply(Matrix.translate(-cx, -cy))
        return m

    @staticmethod
    def skew_x(deg):
        return Matrix(1, 0, math.tan(math.radians(deg)), 1, 0, 0)

    @staticmethod
    def skew_y(deg):
        return Matrix(1, math.tan(math.radians(deg)), 0, 1, 0, 0)


TRANSFORM_FN_RE = re.compile(r"(\w+)\s*\(([^)]*)\)")


def parse_transform(transform_str):
    """Convierte el atributo transform="..." de SVG en una Matrix equivalente."""
    result = Matrix()
    if not transform_str:
        return result
    for name, args_str in TRANSFORM_FN_RE.findall(transform_str):
        args = [float(v) for v in re.findall(r"-?[\d.]+(?:e-?\d+)?", args_str)]
        if name == "matrix" and len(args) == 6:
            fn = Matrix(*args)
        elif name == "translate":
            fn = Matrix.translate(args[0], args[1] if len(args) > 1 else 0.0)
        elif name == "scale":
            fn = Matrix.scale(args[0], args[1] if len(args) > 1 else None)
        elif name == "rotate":
            fn = Matrix.rotate(args[0], args[1] if len(args) > 1 else 0.0,
                                args[2] if len(args) > 2 else 0.0)
        elif name == "skewX" and args:
            fn = Matrix.skew_x(args[0])
        elif name == "skewY" and args:
            fn = Matrix.skew_y(args[0])
        else:
            continue
        result = result.multiply(fn)
    return result


# --------------------------------------------------------------------------
# Parseo de "d" a segmentos canonicos (M, L, C, Z) con coordenadas absolutas.
# Las curvas de Bezier son invariantes bajo transformaciones afines, asi que
# una vez reducido todo a M/L/C basta aplicar la matriz a cada punto.
# --------------------------------------------------------------------------
PATH_TOKEN_RE = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|(-?[0-9.]+(?:[eE][-+]?\d+)?)")


def tokenize_path(d):
    tokens = []
    for cmd, num in PATH_TOKEN_RE.findall(d):
        if cmd:
            tokens.append(cmd)
        elif num:
            tokens.append(float(num))
    return tokens


def arc_to_cubics(x1, y1, rx, ry, phi_deg, large_arc, sweep, x2, y2):
    """Algoritmo estandar SVG arc -> uno o varios cubic bezier (control points absolutos)."""
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return [(x1, y1, x2, y2, x2, y2)]

    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi_deg)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)

    dx2, dy2 = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cos_phi * dx2 + sin_phi * dy2
    y1p = -sin_phi * dx2 + cos_phi * dy2

    lam = (x1p ** 2) / (rx ** 2) + (y1p ** 2) / (ry ** 2)
    if lam > 1:
        scale = math.sqrt(lam)
        rx, ry = rx * scale, ry * scale

    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    co = math.sqrt(max(num / den, 0)) if den else 0
    if large_arc == sweep:
        co = -co
    cxp = co * (rx * y1p / ry)
    cyp = co * -(ry * x1p / rx)

    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    def angle(ux, uy, vx, vy):
        sign = 1 if (ux * vy - uy * vx) >= 0 else -1
        dot = max(-1, min(1, (ux * vx + uy * vy) / (math.hypot(ux, uy) * math.hypot(vx, vy))))
        return sign * math.degrees(math.acos(dot))

    theta1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dtheta = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dtheta > 0:
        dtheta -= 360
    elif sweep and dtheta < 0:
        dtheta += 360

    n_segments = max(1, math.ceil(abs(dtheta) / ARC_SEG_MAX_DEG))
    seg_dtheta = dtheta / n_segments
    alpha = math.sin(math.radians(seg_dtheta)) * (math.sqrt(4 + 3 * math.tan(math.radians(seg_dtheta) / 2) ** 2) - 1) / 3

    cubics = []
    theta = theta1
    px, py = x1, y1
    for _ in range(n_segments):
        theta_rad = math.radians(theta)
        next_theta = theta + seg_dtheta
        next_theta_rad = math.radians(next_theta)

        cos_t, sin_t = math.cos(theta_rad), math.sin(theta_rad)
        cos_nt, sin_nt = math.cos(next_theta_rad), math.sin(next_theta_rad)

        def ellipse_point(ct, st):
            ex = cx + rx * ct * cos_phi - ry * st * sin_phi
            ey = cy + rx * ct * sin_phi + ry * st * cos_phi
            return ex, ey

        def ellipse_deriv(ct, st):
            dex = -rx * st * cos_phi - ry * ct * sin_phi
            dey = -rx * st * sin_phi + ry * ct * cos_phi
            return dex, dey

        p1 = (px, py)
        p2x, p2y = ellipse_point(cos_nt, sin_nt)
        d1x, d1y = ellipse_deriv(cos_t, sin_t)
        d2x, d2y = ellipse_deriv(cos_nt, sin_nt)

        c1 = (p1[0] + alpha * d1x, p1[1] + alpha * d1y)
        c2 = (p2x - alpha * d2x, p2y - alpha * d2y)

        cubics.append((c1[0], c1[1], c2[0], c2[1], p2x, p2y))
        px, py = p2x, p2y
        theta = next_theta

    return cubics


def quad_to_cubic(x0, y0, qx, qy, x, y):
    c1x = x0 + 2.0 / 3.0 * (qx - x0)
    c1y = y0 + 2.0 / 3.0 * (qy - y0)
    c2x = x + 2.0 / 3.0 * (qx - x)
    c2y = y + 2.0 / 3.0 * (qy - y)
    return c1x, c1y, c2x, c2y, x, y


def parse_path_to_canonical(d):
    """Devuelve lista de subpaths; cada subpath es lista de tuplas ('M',x,y)/('L',x,y)/
    ('C',x1,y1,x2,y2,x,y)/('Z',)."""
    tokens = tokenize_path(d)
    i = 0
    subpaths = []
    current = []
    cur_x = cur_y = 0.0
    start_x = start_y = 0.0
    last_cmd = None
    last_ctrl = None  # ultimo punto de control (para S/T), coords absolutas

    def read_nums(count):
        nonlocal i
        vals = tokens[i:i + count]
        i += count
        return vals

    while i < len(tokens):
        tok = tokens[i]
        if isinstance(tok, str):
            cmd = tok
            i += 1
        else:
            cmd = last_cmd
        is_rel = cmd.islower()
        c = cmd.upper()

        if c == "M":
            if current:
                subpaths.append(current)
            x, y = read_nums(2)
            if is_rel and last_cmd is not None:
                x, y = cur_x + x, cur_y + y
            cur_x, cur_y = x, y
            start_x, start_y = x, y
            current = [("M", x, y)]
            last_ctrl = None
        elif c == "L":
            x, y = read_nums(2)
            if is_rel:
                x, y = cur_x + x, cur_y + y
            current.append(("L", x, y))
            cur_x, cur_y = x, y
            last_ctrl = None
        elif c == "H":
            (x,) = read_nums(1)
            x = cur_x + x if is_rel else x
            current.append(("L", x, cur_y))
            cur_x = x
            last_ctrl = None
        elif c == "V":
            (y,) = read_nums(1)
            y = cur_y + y if is_rel else y
            current.append(("L", cur_x, y))
            cur_y = y
            last_ctrl = None
        elif c == "C":
            x1, y1, x2, y2, x, y = read_nums(6)
            if is_rel:
                x1, y1, x2, y2, x, y = cur_x + x1, cur_y + y1, cur_x + x2, cur_y + y2, cur_x + x, cur_y + y
            current.append(("C", x1, y1, x2, y2, x, y))
            cur_x, cur_y = x, y
            last_ctrl = (x2, y2)
        elif c == "S":
            x2, y2, x, y = read_nums(4)
            if is_rel:
                x2, y2, x, y = cur_x + x2, cur_y + y2, cur_x + x, cur_y + y
            if last_ctrl and last_cmd and last_cmd.upper() in ("C", "S"):
                x1, y1 = 2 * cur_x - last_ctrl[0], 2 * cur_y - last_ctrl[1]
            else:
                x1, y1 = cur_x, cur_y
            current.append(("C", x1, y1, x2, y2, x, y))
            cur_x, cur_y = x, y
            last_ctrl = (x2, y2)
        elif c == "Q":
            qx, qy, x, y = read_nums(4)
            if is_rel:
                qx, qy, x, y = cur_x + qx, cur_y + qy, cur_x + x, cur_y + y
            current.append(("C", *quad_to_cubic(cur_x, cur_y, qx, qy, x, y)))
            cur_x, cur_y = x, y
            last_ctrl = (qx, qy)
        elif c == "T":
            x, y = read_nums(2)
            if is_rel:
                x, y = cur_x + x, cur_y + y
            if last_ctrl and last_cmd and last_cmd.upper() in ("Q", "T"):
                qx, qy = 2 * cur_x - last_ctrl[0], 2 * cur_y - last_ctrl[1]
            else:
                qx, qy = cur_x, cur_y
            current.append(("C", *quad_to_cubic(cur_x, cur_y, qx, qy, x, y)))
            cur_x, cur_y = x, y
            last_ctrl = (qx, qy)
        elif c == "A":
            rx, ry, rot, large, sweep, x, y = read_nums(7)
            if is_rel:
                x, y = cur_x + x, cur_y + y
            for cubic in arc_to_cubics(cur_x, cur_y, rx, ry, rot, bool(large), bool(sweep), x, y):
                current.append(("C", *cubic))
            cur_x, cur_y = x, y
            last_ctrl = None
        elif c == "Z":
            current.append(("Z",))
            cur_x, cur_y = start_x, start_y
            last_ctrl = None
        else:
            raise ValueError(f"Comando de path no soportado: {cmd}")

        last_cmd = cmd

    if current:
        subpaths.append(current)
    return subpaths


def transform_subpaths(subpaths, matrix):
    out = []
    for sub in subpaths:
        new_sub = []
        for seg in sub:
            if seg[0] == "M" or seg[0] == "L":
                x, y = matrix.apply(seg[1], seg[2])
                new_sub.append((seg[0], x, y))
            elif seg[0] == "C":
                x1, y1 = matrix.apply(seg[1], seg[2])
                x2, y2 = matrix.apply(seg[3], seg[4])
                x, y = matrix.apply(seg[5], seg[6])
                new_sub.append(("C", x1, y1, x2, y2, x, y))
            else:
                new_sub.append(seg)
        out.append(new_sub)
    return out


def fmt(n):
    return f"{n:.3f}".rstrip("0").rstrip(".") if "." in f"{n:.3f}" else f"{n:.3f}"


def subpaths_to_d(subpaths):
    parts = []
    for sub in subpaths:
        for seg in sub:
            if seg[0] == "M":
                parts.append(f"M{fmt(seg[1])},{fmt(seg[2])}")
            elif seg[0] == "L":
                parts.append(f"L{fmt(seg[1])},{fmt(seg[2])}")
            elif seg[0] == "C":
                parts.append(
                    f"C{fmt(seg[1])},{fmt(seg[2])} {fmt(seg[3])},{fmt(seg[4])} {fmt(seg[5])},{fmt(seg[6])}"
                )
            elif seg[0] == "Z":
                parts.append("Z")
    return " ".join(parts)


# --------------------------------------------------------------------------
# Conversion de figuras basicas a subpaths canonicos (antes de transformar)
# --------------------------------------------------------------------------
CIRCLE_K = 0.5522847498307936


def ellipse_to_subpaths(cx, cy, rx, ry):
    k_x, k_y = rx * CIRCLE_K, ry * CIRCLE_K
    return [[
        ("M", cx + rx, cy),
        ("C", cx + rx, cy + k_y, cx + k_x, cy + ry, cx, cy + ry),
        ("C", cx - k_x, cy + ry, cx - rx, cy + k_y, cx - rx, cy),
        ("C", cx - rx, cy - k_y, cx - k_x, cy - ry, cx, cy - ry),
        ("C", cx + k_x, cy - ry, cx + rx, cy - k_y, cx + rx, cy),
        ("Z",),
    ]]


def rect_to_subpaths(x, y, w, h, rx, ry):
    if not rx and not ry:
        return [[("M", x, y), ("L", x + w, y), ("L", x + w, y + h), ("L", x, y + h), ("Z",)]]
    rx = rx or ry
    ry = ry or rx
    rx, ry = min(rx, w / 2.0), min(ry, h / 2.0)
    k_x, k_y = rx * CIRCLE_K, ry * CIRCLE_K
    return [[
        ("M", x + rx, y),
        ("L", x + w - rx, y),
        ("C", x + w - rx + k_x, y, x + w, y + ry - k_y, x + w, y + ry),
        ("L", x + w, y + h - ry),
        ("C", x + w, y + h - ry + k_y, x + w - rx + k_x, y + h, x + w - rx, y + h),
        ("L", x + rx, y + h),
        ("C", x + rx - k_x, y + h, x, y + h - ry + k_y, x, y + h - ry),
        ("L", x, y + ry),
        ("C", x, y + ry - k_y, x + rx - k_x, y, x + rx, y),
        ("Z",),
    ]]


def points_to_subpaths(points_str, close):
    nums = [float(v) for v in re.findall(r"-?[\d.]+(?:e-?\d+)?", points_str)]
    pts = list(zip(nums[0::2], nums[1::2]))
    if not pts:
        return []
    sub = [("M", pts[0][0], pts[0][1])] + [("L", x, y) for x, y in pts[1:]]
    if close:
        sub.append(("Z",))
    return [sub]


def line_to_subpaths(x1, y1, x2, y2):
    return [[("M", x1, y1), ("L", x2, y2)]]


# --------------------------------------------------------------------------
# Color / estilo
# --------------------------------------------------------------------------
def parse_color(value):
    if value is None:
        return None
    value = value.strip()
    if value in ("none", "transparent"):
        return None
    if value == "currentColor":
        return "#ff000000"
    if value in NAMED_COLORS:
        hexval = NAMED_COLORS[value]
        return to_argb(hexval) if hexval else None
    if value.startswith("#"):
        return to_argb(value)
    m = re.match(r"rgba?\(\s*([\d.]+%?)\s*,\s*([\d.]+%?)\s*,\s*([\d.]+%?)\s*(?:,\s*([\d.]+)\s*)?\)", value)
    if m:
        def chan(v):
            return round(float(v[:-1]) * 2.55) if v.endswith("%") else round(float(v))
        r, g, b = chan(m.group(1)), chan(m.group(2)), chan(m.group(3))
        a = round(float(m.group(4)) * 255) if m.group(4) else 255
        return f"#{a:02x}{r:02x}{g:02x}{b:02x}"
    return "#ff000000"


def to_argb(hex_color):
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
        return f"#ff{h}"
    if len(h) == 6:
        return f"#ff{h}"
    if len(h) == 8:
        return f"#{h[6:8]}{h[0:6]}"
    return "#ff000000"


STYLE_ATTRS = ("fill", "stroke", "stroke-width", "opacity", "fill-opacity",
               "stroke-opacity", "fill-rule")


def parse_style_attr(style_str):
    result = {}
    for decl in style_str.split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            result[k.strip()] = v.strip()
    return result


class Style:
    def __init__(self, fill="#ff000000", stroke=None, stroke_width=1.0,
                 opacity=1.0, fill_opacity=1.0, stroke_opacity=1.0, fill_rule="nonzero"):
        self.fill = fill
        self.stroke = stroke
        self.stroke_width = stroke_width
        self.opacity = opacity
        self.fill_opacity = fill_opacity
        self.stroke_opacity = stroke_opacity
        self.fill_rule = fill_rule

    def inherit(self, elem):
        attrs = dict(elem.attrib)
        if "style" in elem.attrib:
            attrs.update(parse_style_attr(elem.attrib["style"]))

        fill = attrs.get("fill", self.fill)
        stroke = attrs.get("stroke", self.stroke)
        stroke_width = float(attrs["stroke-width"]) if "stroke-width" in attrs else self.stroke_width
        opacity = float(attrs["opacity"]) if "opacity" in attrs else self.opacity
        fill_opacity = float(attrs["fill-opacity"]) if "fill-opacity" in attrs else self.fill_opacity
        stroke_opacity = float(attrs["stroke-opacity"]) if "stroke-opacity" in attrs else self.stroke_opacity
        fill_rule = attrs.get("fill-rule", self.fill_rule)
        return Style(fill, stroke, stroke_width, opacity, fill_opacity, stroke_opacity, fill_rule)


# --------------------------------------------------------------------------
# Recorrido del arbol SVG
# --------------------------------------------------------------------------
class PathItem:
    def __init__(self, name, d, style):
        self.name = name
        self.d = d
        self.style = style


def strip_ns(tag):
    return tag.split("}")[-1] if "}" in tag else tag


def walk(elem, matrix, style, items, counter):
    tag = strip_ns(elem.tag)
    if tag in ("defs", "clipPath", "mask", "symbol", "title", "desc", "style", "metadata"):
        return
    local_matrix = matrix.multiply(parse_transform(elem.attrib.get("transform", "")))
    local_style = style.inherit(elem)

    if tag == "g" or tag == "svg" or tag == "a":
        for child in elem:
            walk(child, local_matrix, local_style, items, counter)
        return

    subpaths = None
    if tag == "path" and "d" in elem.attrib:
        subpaths = parse_path_to_canonical(elem.attrib["d"])
    elif tag == "rect":
        x, y = float(elem.attrib.get("x", 0)), float(elem.attrib.get("y", 0))
        w, h = float(elem.attrib.get("width", 0)), float(elem.attrib.get("height", 0))
        rx = float(elem.attrib["rx"]) if "rx" in elem.attrib else None
        ry = float(elem.attrib["ry"]) if "ry" in elem.attrib else None
        if w > 0 and h > 0:
            subpaths = rect_to_subpaths(x, y, w, h, rx, ry)
    elif tag == "circle":
        cx, cy = float(elem.attrib.get("cx", 0)), float(elem.attrib.get("cy", 0))
        r = float(elem.attrib.get("r", 0))
        if r > 0:
            subpaths = ellipse_to_subpaths(cx, cy, r, r)
    elif tag == "ellipse":
        cx, cy = float(elem.attrib.get("cx", 0)), float(elem.attrib.get("cy", 0))
        rx, ry = float(elem.attrib.get("rx", 0)), float(elem.attrib.get("ry", 0))
        if rx > 0 and ry > 0:
            subpaths = ellipse_to_subpaths(cx, cy, rx, ry)
    elif tag == "line":
        subpaths = line_to_subpaths(
            float(elem.attrib.get("x1", 0)), float(elem.attrib.get("y1", 0)),
            float(elem.attrib.get("x2", 0)), float(elem.attrib.get("y2", 0)))
    elif tag in ("polyline", "polygon") and "points" in elem.attrib:
        subpaths = points_to_subpaths(elem.attrib["points"], close=(tag == "polygon"))
    elif tag == "use":
        print(f"Aviso: elemento <use> ignorado (no soportado): {elem.attrib.get('{http://www.w3.org/1999/xlink}href', '?')}",
              file=sys.stderr)
        return
    else:
        return

    if not subpaths:
        return

    subpaths = transform_subpaths(subpaths, local_matrix)
    counter[0] += 1
    name = elem.attrib.get("id") or f"path_{counter[0]}"
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if not re.match(r"[a-zA-Z_]", name):
        name = f"p_{name}"
    items.append(PathItem(name, subpaths_to_d(subpaths), local_style))


LENGTH_RE = re.compile(r"[-\d.]+")


def parse_length(value, default=None):
    if value is None:
        return default
    m = LENGTH_RE.match(value.strip())
    return float(m.group()) if m else default


def load_svg(svg_path):
    tree = ET.parse(svg_path)
    root = tree.getroot()

    view_box = root.attrib.get("viewBox")
    if view_box:
        min_x, min_y, vb_w, vb_h = (float(v) for v in re.split(r"[\s,]+", view_box.strip()))
    else:
        min_x = min_y = 0.0
        vb_w = parse_length(root.attrib.get("width"), 24.0)
        vb_h = parse_length(root.attrib.get("height"), 24.0)

    width = parse_length(root.attrib.get("width"), vb_w)
    height = parse_length(root.attrib.get("height"), vb_h)

    root_matrix = Matrix.translate(-min_x, -min_y)
    base_style = Style()
    items = []
    counter = [0]
    walk(root, root_matrix, base_style, items, counter)

    if not items:
        raise ValueError("No se encontraron figuras soportadas (path/rect/circle/ellipse/line/polyline/polygon) en el SVG.")

    return items, vb_w, vb_h, width, height


# --------------------------------------------------------------------------
# Generacion de XML (vector / animated-vector / animator)
# --------------------------------------------------------------------------
def set_android_attr(elem, name, value):
    elem.set(f"{{{ANDROID_NS}}}{name}", value)


def register_namespaces():
    ET.register_namespace("android", ANDROID_NS)
    ET.register_namespace("aapt", AAPT_NS)


def build_vector_xml(items, vb_w, vb_h, width_dp, height_dp, group_name):
    vector = ET.Element("vector")
    set_android_attr(vector, "width", f"{width_dp:g}dp")
    set_android_attr(vector, "height", f"{height_dp:g}dp")
    set_android_attr(vector, "viewportWidth", f"{vb_w:g}")
    set_android_attr(vector, "viewportHeight", f"{vb_h:g}")

    group = ET.SubElement(vector, "group")
    set_android_attr(group, "name", group_name)
    set_android_attr(group, "pivotX", f"{vb_w / 2:g}")
    set_android_attr(group, "pivotY", f"{vb_h / 2:g}")

    for item in items:
        path = ET.SubElement(group, "path")
        set_android_attr(path, "name", item.name)
        set_android_attr(path, "pathData", item.d)
        style = item.style
        if style.fill:
            fill_argb = parse_color(style.fill)
            if fill_argb:
                set_android_attr(path, "fillColor", fill_argb)
                fill_alpha = style.fill_opacity * style.opacity
                if fill_alpha < 1.0:
                    set_android_attr(path, "fillAlpha", f"{fill_alpha:g}")
                if style.fill_rule == "evenodd":
                    set_android_attr(path, "fillType", "evenOdd")
        if style.stroke and style.stroke not in ("none",):
            stroke_argb = parse_color(style.stroke)
            if stroke_argb:
                set_android_attr(path, "strokeColor", stroke_argb)
                set_android_attr(path, "strokeWidth", f"{style.stroke_width:g}")
                stroke_alpha = style.stroke_opacity * style.opacity
                if stroke_alpha < 1.0:
                    set_android_attr(path, "strokeAlpha", f"{stroke_alpha:g}")
    return vector


def build_animated_vector_xml(drawable_name, targets):
    """targets: lista de (target_name, animator_resource_name)"""
    av = ET.Element("animated-vector")
    set_android_attr(av, "drawable", f"@drawable/{drawable_name}")
    for target_name, animator_name in targets:
        target = ET.SubElement(av, "target")
        set_android_attr(target, "name", target_name)
        set_android_attr(target, "animation", f"@animator/{animator_name}")
    return av


def objector(property_name, value_from, value_to, duration, start_offset=0,
             repeat_count=None, repeat_mode=None, interpolator=None, value_type="floatType"):
    oa = ET.Element("objectAnimator")
    set_android_attr(oa, "propertyName", property_name)
    set_android_attr(oa, "valueFrom", f"{value_from:g}" if isinstance(value_from, (int, float)) else str(value_from))
    set_android_attr(oa, "valueTo", f"{value_to:g}" if isinstance(value_to, (int, float)) else str(value_to))
    set_android_attr(oa, "valueType", value_type)
    set_android_attr(oa, "duration", str(duration))
    if start_offset:
        set_android_attr(oa, "startOffset", str(start_offset))
    if repeat_count is not None:
        set_android_attr(oa, "repeatCount", str(repeat_count))
    if repeat_mode:
        set_android_attr(oa, "repeatMode", repeat_mode)
    if interpolator:
        set_android_attr(oa, "interpolator", interpolator)
    return oa


def build_rotate_animator(duration):
    root = objector("rotation", 0, 360, duration, repeat_count="infinite",
                    interpolator="@android:anim/linear_interpolator")
    return root


def build_fade_animator(duration):
    root = ET.Element("set")
    set_android_attr(root, "ordering", "together")
    half = max(duration // 2, 1)
    root.append(objector("alpha", 0, 1, half, repeat_count="infinite", repeat_mode="reverse",
                          interpolator="@android:anim/decelerate_interpolator"))
    root.append(objector("scaleX", 0.85, 1.0, half, repeat_count="infinite", repeat_mode="reverse",
                          interpolator="@android:anim/decelerate_interpolator"))
    root.append(objector("scaleY", 0.85, 1.0, half, repeat_count="infinite", repeat_mode="reverse",
                          interpolator="@android:anim/decelerate_interpolator"))
    return root


def build_draw_animator(duration, start_offset):
    root = objector("trimPathEnd", 0, 1, duration, start_offset=start_offset,
                     interpolator="@android:anim/accelerate_decelerate_interpolator")
    return root


def write_xml(elem, path):
    register_namespaces()
    rough = ET.tostring(elem, encoding="unicode")
    pretty = minidom.parseString(rough).toprettyxml(indent="    ")
    lines = [line for line in pretty.split("\n") if line.strip()]
    lines[0] = '<?xml version="1.0" encoding="utf-8"?>'
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  escrito {path}")


def aget(elem, name, default=None):
    return elem.attrib.get(f"{{{ANDROID_NS}}}{name}", default)


aset = set_android_attr

COLOR_ATTRS = ("fillColor", "strokeColor")
FLOAT_ATTRS = ("strokeWidth", "fillAlpha", "strokeAlpha", "trimPathStart", "trimPathEnd",
               "trimPathOffset", "rotation", "pivotX", "pivotY", "translateX", "translateY",
               "scaleX", "scaleY", "alpha")
ALPHA_ATTRS = ("fillAlpha", "strokeAlpha", "trimPathStart", "trimPathEnd", "trimPathOffset")
KNOWN_PROPERTY_NAMES = {
    "rotation", "translateX", "translateY", "scaleX", "scaleY", "alpha", "pathData",
    "trimPathStart", "trimPathEnd", "trimPathOffset", "fillColor", "strokeColor",
    "fillAlpha", "strokeAlpha", "strokeWidth", "pivotX", "pivotY",
}


# --------------------------------------------------------------------------
# Verificacion / reparacion de recursos ya generados (vector, animated-vector,
# objectAnimator/set). Las funciones analyze_* detectan problemas y, cuando
# apply_fix=True, corrigen lo que se puede corregir sin adivinar la intencion
# del autor (dejando lo demas reportado como no resuelto).
# --------------------------------------------------------------------------
@dataclass
class Finding:
    level: str  # "ERROR" | "WARNING"
    location: str
    message: str
    fixed: bool = False


def has_android_namespace_decl(raw_text):
    return re.search(r'xmlns:\w+\s*=\s*["\']' + re.escape(ANDROID_NS) + r'["\']', raw_text) is not None


def fix_color(value):
    v = value.strip().lstrip("#")
    if re.match(r"^[0-9a-fA-F]{3}$", v):
        return to_argb(f"#{v}")
    if re.match(r"^[0-9a-fA-F]{6}$", v):
        return f"#ff{v}"
    if re.match(r"^[0-9a-fA-F]{8}$", v):
        return f"#{v}"
    named = NAMED_COLORS.get(value.strip().lower())
    if named:
        return to_argb(named)
    return None


def collect_names(root):
    names = set()
    for elem in root.iter():
        if strip_ns(elem.tag) in ("group", "path", "clip-path"):
            name = aget(elem, "name")
            if name:
                names.add(name)
    return names


def analyze_vector(root, raw_text, label, apply_fix):
    findings = []

    def add(level, loc, msg, fixed=False):
        findings.append(Finding(level, loc, msg, fixed))

    if strip_ns(root.tag) != "vector":
        add("ERROR", label, f"La raiz es <{strip_ns(root.tag)}>, se esperaba <vector>.")
        return findings

    if not has_android_namespace_decl(raw_text):
        add("ERROR", label, "Falta la declaracion xmlns:android en la raiz <vector>.", fixed=apply_fix)

    width, height = aget(root, "width"), aget(root, "height")
    vb_w, vb_h = aget(root, "viewportWidth"), aget(root, "viewportHeight")

    if not vb_w or not vb_h:
        base = width or height
        if apply_fix and base:
            derived = parse_length(base)
            if derived:
                aset(root, "viewportWidth", vb_w or f"{derived:g}")
                aset(root, "viewportHeight", vb_h or f"{derived:g}")
                add("ERROR", label, "Faltaba android:viewportWidth/viewportHeight; se derivo de width/height.", fixed=True)
                vb_w, vb_h = aget(root, "viewportWidth"), aget(root, "viewportHeight")
        if not vb_w or not vb_h:
            add("ERROR", label, "Falta android:viewportWidth y/o android:viewportHeight.")
    else:
        for attr_name, val in (("viewportWidth", vb_w), ("viewportHeight", vb_h)):
            try:
                if float(val) <= 0:
                    add("ERROR", label, f"android:{attr_name}='{val}' debe ser mayor que 0.")
            except ValueError:
                add("ERROR", label, f"android:{attr_name}='{val}' no es numerico.")

    for attr_name, val in (("width", width), ("height", height)):
        if not val:
            base = vb_w if attr_name == "width" else vb_h
            if apply_fix and base:
                try:
                    aset(root, attr_name, f"{float(base):g}dp")
                    add("ERROR", label, f"Faltaba android:{attr_name}; se asigno {float(base):g}dp a partir del viewport.", fixed=True)
                    continue
                except ValueError:
                    pass
            add("ERROR", label, f"Falta android:{attr_name}.")
        elif not re.match(r"^-?[\d.]+(dp|dip|px|sp|pt|in|mm)$", val):
            if apply_fix:
                aset(root, attr_name, f"{val}dp")
                add("WARNING", label, f"android:{attr_name}='{val}' no tenia unidad; se le agrego 'dp'.", fixed=True)
            else:
                add("WARNING", label, f"android:{attr_name}='{val}' no tiene unidad (dp/px/...).")

    names_seen = set()
    counter = [0]

    def unique_name(prefix):
        counter[0] += 1
        candidate = f"{prefix}_{counter[0]}"
        while candidate in names_seen:
            counter[0] += 1
            candidate = f"{prefix}_{counter[0]}"
        return candidate

    def walk_children(elem, parent_loc):
        for child in list(elem):
            ctag = strip_ns(child.tag)
            if ctag not in ("group", "path", "clip-path"):
                add("WARNING", parent_loc, f"Elemento hijo desconocido <{ctag}> ignorado.")
                continue
            loc = f"{label} :: <{ctag}>"
            name = aget(child, "name")
            if not name:
                if apply_fix:
                    name = unique_name(ctag)
                    aset(child, "name", name)
                    names_seen.add(name)
                    add("WARNING", loc, f"Sin android:name; se asigno '{name}'.", fixed=True)
                else:
                    add("WARNING", loc, "Sin android:name (no podra usarse como target de animacion individual).")
            else:
                if name in names_seen:
                    if apply_fix:
                        old_name = name
                        name = unique_name(old_name)
                        aset(child, "name", name)
                        add("ERROR", loc, f"android:name='{old_name}' duplicado; renombrado a '{name}' "
                                           f"(revisa referencias a este nombre en el animated-vector).", fixed=True)
                    else:
                        add("ERROR", loc, f"android:name='{name}' duplicado.")
                names_seen.add(name)
            loc = f"{label} :: {ctag} '{name or '(sin nombre)'}'"

            if ctag in ("path", "clip-path"):
                d = aget(child, "pathData")
                if not d or not d.strip():
                    add("ERROR", loc, "Falta android:pathData o esta vacio.")
                elif not re.match(r"^[MmLlHhVvCcSsQqTtAaZz0-9.,\-\seE]+$", d):
                    add("ERROR", loc, "android:pathData contiene caracteres no validos.")
                else:
                    try:
                        parse_path_to_canonical(d)
                    except Exception as exc:
                        add("ERROR", loc, f"android:pathData no se pudo interpretar: {exc}")

            for cattr in COLOR_ATTRS:
                val = aget(child, cattr)
                if not val or val.startswith("@"):
                    continue
                if not re.match(r"^#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$", val):
                    fixed_val = fix_color(val) if apply_fix else None
                    if fixed_val:
                        aset(child, cattr, fixed_val)
                        add("ERROR", loc, f"android:{cattr}='{val}' con formato invalido; corregido a '{fixed_val}'.", fixed=True)
                    else:
                        add("ERROR", loc, f"android:{cattr}='{val}' tiene un formato de color invalido "
                                           f"(usa #RRGGBB o #AARRGGBB).")

            for fattr in FLOAT_ATTRS:
                val = aget(child, fattr)
                if val is None:
                    continue
                try:
                    fval = float(val)
                except ValueError:
                    add("ERROR", loc, f"android:{fattr}='{val}' no es numerico.")
                    continue
                if fattr in ALPHA_ATTRS and not (0.0 <= fval <= 1.0):
                    if apply_fix:
                        clamped = max(0.0, min(1.0, fval))
                        aset(child, fattr, f"{clamped:g}")
                        add("WARNING", loc, f"android:{fattr}={fval:g} fuera de [0,1]; ajustado a {clamped:g}.", fixed=True)
                    else:
                        add("WARNING", loc, f"android:{fattr}={fval:g} deberia estar en [0,1].")

            if ctag == "group":
                walk_children(child, loc)

    walk_children(root, label)
    return findings


def analyze_animated_vector(root, raw_text, label, vector_names, vector_stem, animator_dir, apply_fix):
    findings = []

    def add(level, loc, msg, fixed=False):
        findings.append(Finding(level, loc, msg, fixed))

    if strip_ns(root.tag) != "animated-vector":
        add("ERROR", label, f"La raiz es <{strip_ns(root.tag)}>, se esperaba <animated-vector>.")
        return findings

    if not has_android_namespace_decl(raw_text):
        add("ERROR", label, "Falta la declaracion xmlns:android en la raiz <animated-vector>.", fixed=apply_fix)

    drawable = aget(root, "drawable")
    m = re.match(r"^@drawable/(\w+)$", drawable or "")
    if not m:
        add("ERROR", label, "Falta o es invalido android:drawable (debe ser @drawable/nombre).")
    elif vector_stem and m.group(1) != vector_stem:
        if apply_fix:
            aset(root, "drawable", f"@drawable/{vector_stem}")
            add("ERROR", label, f"android:drawable apuntaba a '@drawable/{m.group(1)}', no coincide con el "
                                 f"vector proporcionado; corregido a '@drawable/{vector_stem}'.", fixed=True)
        else:
            add("WARNING", label, f"android:drawable='@drawable/{m.group(1)}' no coincide con el archivo "
                                   f"vector proporcionado ('{vector_stem}').")

    seen = set()
    for target in root.findall("target"):
        tname = aget(target, "name")
        tanim = aget(target, "animation")
        loc = f"{label} :: target '{tname or '?'}'"
        if not tname:
            add("ERROR", loc, "Falta android:name en <target>.")
        else:
            if tname in seen:
                add("ERROR", loc, f"target con android:name='{tname}' duplicado.")
            seen.add(tname)
            if vector_names is not None and tname not in vector_names:
                add("ERROR", loc, f"El target '{tname}' no existe entre los paths/groups del vector proporcionado.")

        am = re.match(r"^@animator/(\w+)$", tanim or "")
        if not am:
            add("ERROR", loc, "Falta o es invalido android:animation (debe ser @animator/nombre).")
        elif animator_dir:
            candidate = animator_dir / f"{am.group(1)}.xml"
            if not candidate.is_file():
                add("WARNING", loc, f"No se encontro el recurso @animator/{am.group(1)} en '{animator_dir}'.")
    return findings


def analyze_animator(elem, label, apply_fix, path=""):
    findings = []

    def add(level, loc, msg, fixed=False):
        findings.append(Finding(level, loc, msg, fixed))

    tag = strip_ns(elem.tag)
    loc = f"{label}{path}"

    if tag == "set":
        ordering = aget(elem, "ordering")
        if ordering and ordering not in ("together", "sequentially"):
            add("ERROR", loc, f"android:ordering='{ordering}' invalido (usa 'together' o 'sequentially').")
        for i, child in enumerate(elem):
            findings.extend(analyze_animator(child, label, apply_fix, f"{path}/{strip_ns(child.tag)}[{i}]"))
        return findings

    if tag != "objectAnimator":
        add("WARNING", loc, f"Elemento <{tag}> no reconocido dentro de un recurso animator.")
        return findings

    prop = aget(elem, "propertyName")
    if not prop:
        add("ERROR", loc, "Falta android:propertyName.")
    elif prop not in KNOWN_PROPERTY_NAMES:
        add("WARNING", loc, f"android:propertyName='{prop}' no es una propiedad animable conocida de VectorDrawable.")

    duration = aget(elem, "duration")
    if duration is None:
        if apply_fix:
            aset(elem, "duration", "300")
            add("WARNING", loc, "Falta android:duration; se asigno 300 (ms) por defecto.", fixed=True)
        else:
            add("WARNING", loc, "Falta android:duration (se usara el valor por defecto del sistema, 300ms).")
    else:
        try:
            if int(duration) < 0:
                add("ERROR", loc, "android:duration no puede ser negativo.")
        except ValueError:
            add("ERROR", loc, f"android:duration='{duration}' no es un entero valido.")

    start_offset = aget(elem, "startOffset")
    if start_offset is not None:
        try:
            int(start_offset)
        except ValueError:
            add("ERROR", loc, f"android:startOffset='{start_offset}' no es un entero valido.")

    repeat_count = aget(elem, "repeatCount")
    if repeat_count is not None and repeat_count != "infinite":
        try:
            int(repeat_count)
        except ValueError:
            add("ERROR", loc, f"android:repeatCount='{repeat_count}' debe ser entero o 'infinite'.")

    repeat_mode = aget(elem, "repeatMode")
    if repeat_mode and repeat_mode not in ("restart", "reverse"):
        add("ERROR", loc, f"android:repeatMode='{repeat_mode}' invalido (usa 'restart' o 'reverse').")

    if prop in KNOWN_PROPERTY_NAMES:
        expected_type = ("colorType" if prop in ("fillColor", "strokeColor")
                          else "pathType" if prop == "pathData" else "floatType")
        value_type = aget(elem, "valueType")
        if value_type and value_type != expected_type:
            if apply_fix:
                aset(elem, "valueType", expected_type)
                add("WARNING", loc, f"android:valueType='{value_type}' no coincide con la propiedad '{prop}'; "
                                     f"corregido a '{expected_type}'.", fixed=True)
            else:
                add("WARNING", loc, f"android:valueType='{value_type}' probablemente deberia ser "
                                     f"'{expected_type}' para la propiedad '{prop}'.")
        if expected_type == "floatType":
            for attr_name in ("valueFrom", "valueTo"):
                val = aget(elem, attr_name)
                if val is not None:
                    try:
                        float(val)
                    except ValueError:
                        add("ERROR", loc, f"android:{attr_name}='{val}' no es numerico.")
    return findings


def print_findings(findings, title):
    print(f"\n== {title} ==")
    if not findings:
        print("  Sin problemas detectados.")
        return
    for f in findings:
        tag = "[FIXED]" if f.fixed else f"[{f.level}]"
        print(f"  {tag} {f.location}: {f.message}")
    errors = sum(1 for f in findings if f.level == "ERROR" and not f.fixed)
    warnings = sum(1 for f in findings if f.level == "WARNING" and not f.fixed)
    fixed = sum(1 for f in findings if f.fixed)
    print(f"  -> {errors} error(es), {warnings} advertencia(s) sin resolver, {fixed} corregido(s).")


def animator_dir_for(drawable_file):
    """Heuristica: res/drawable/x.xml -> res/animator/, si no, carpeta hermana 'animator'."""
    if drawable_file.parent.name == "drawable":
        return drawable_file.parent.parent / "animator"
    return drawable_file.parent / "animator"


def summarize_animator(root):
    tag = strip_ns(root.tag)
    if tag == "objectAnimator":
        prop = aget(root, "propertyName")
        vf, vt = aget(root, "valueFrom"), aget(root, "valueTo")
        dur = aget(root, "duration", "300")
        bits = [f"{prop} {vf}->{vt}", f"dur={dur}ms"]
        off = aget(root, "startOffset")
        rep = aget(root, "repeatCount")
        if off:
            bits.append(f"offset={off}ms")
        if rep:
            bits.append(f"repeat={rep}")
        return " ".join(bits)
    if tag == "set":
        return "set(" + "; ".join(summarize_animator(c) for c in root) + ")"
    return f"<{tag}>"


def compute_bbox(d):
    try:
        subpaths = parse_path_to_canonical(d)
    except Exception:
        return None
    xs, ys = [], []
    for sub in subpaths:
        for seg in sub:
            if seg[0] in ("M", "L"):
                xs.append(seg[1])
                ys.append(seg[2])
            elif seg[0] == "C":
                xs.extend([seg[1], seg[3], seg[5]])
                ys.extend([seg[2], seg[4], seg[6]])
    if not xs:
        return None
    return round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def sanitize_resource_name(name):
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name).lower()
    if not re.match(r"[a-z_]", name):
        name = f"ic_{name}"
    return name


def cmd_convert(args):
    if not args.svg_file.is_file():
        print(f"No existe el archivo: {args.svg_file}", file=sys.stderr)
        return 1

    base_name = sanitize_resource_name(args.name or args.svg_file.stem)

    try:
        items, vb_w, vb_h, width, height = load_svg(args.svg_file)
    except Exception as exc:
        print(f"Error al procesar el SVG: {exc}", file=sys.stderr)
        return 1

    width_dp = args.width or width
    height_dp = args.height or height

    drawable_dir = args.out_dir / "drawable"
    animator_dir = args.out_dir / "animator"
    drawable_dir.mkdir(parents=True, exist_ok=True)
    animator_dir.mkdir(parents=True, exist_ok=True)

    group_name = f"{base_name}_group"
    vector_xml = build_vector_xml(items, vb_w, vb_h, width_dp, height_dp, group_name)
    write_xml(vector_xml, drawable_dir / f"{base_name}.xml")

    targets = []
    if args.animation == "rotate":
        animator_name = f"{base_name}_rotate"
        write_xml(build_rotate_animator(args.duration), animator_dir / f"{animator_name}.xml")
        targets.append((group_name, animator_name))
    elif args.animation == "fade":
        animator_name = f"{base_name}_fade"
        write_xml(build_fade_animator(args.duration), animator_dir / f"{animator_name}.xml")
        targets.append((group_name, animator_name))
    elif args.animation == "draw":
        if not any(item.style.stroke for item in items):
            print("Aviso: ningun path tiene 'stroke'; trimPathStart/End no producira efecto visible "
                  "en paths sin trazo (solo afecta al stroke, no al fill).", file=sys.stderr)
        for idx, item in enumerate(items):
            animator_name = f"{base_name}_draw_{idx}"
            write_xml(build_draw_animator(args.duration, idx * args.stagger),
                      animator_dir / f"{animator_name}.xml")
            targets.append((item.name, animator_name))

    animated_vector_xml = build_animated_vector_xml(base_name, targets)
    write_xml(animated_vector_xml, drawable_dir / f"{base_name}_animated.xml")

    print(f"\nListo. Usa @drawable/{base_name}_animated en tu layout/ImageView, "
          f"o AnimatedVectorDrawableCompat.create(context, R.drawable.{base_name}_animated).")
    return 0


def _read_and_parse(path):
    raw = path.read_text(encoding="utf-8")
    return ET.fromstring(raw), raw


def cmd_verify(args):
    parsed = {}
    for f in args.files:
        if not f.is_file():
            print(f"[ERROR] {f}: no existe el archivo.", file=sys.stderr)
            return 1
        try:
            parsed[f] = _read_and_parse(f)
        except ET.ParseError as exc:
            print(f"[ERROR] {f}: no se pudo parsear como XML: {exc}", file=sys.stderr)
            return 1

    vector_entry = next(((f, r, raw) for f, (r, raw) in parsed.items() if strip_ns(r.tag) == "vector"), None)
    animated_entry = next(((f, r, raw) for f, (r, raw) in parsed.items() if strip_ns(r.tag) == "animated-vector"), None)
    animator_entries = [(f, r) for f, (r, raw) in parsed.items() if strip_ns(r.tag) in ("objectAnimator", "set")]
    recognized = {vector_entry[0] if vector_entry else None, animated_entry[0] if animated_entry else None}
    recognized.update(f for f, _ in animator_entries)

    if not vector_entry and not animated_entry and not animator_entries:
        print("Ninguno de los archivos tiene una raiz reconocida (<vector>, <animated-vector>, "
              "<objectAnimator> o <set>).", file=sys.stderr)
        return 1

    exit_code = 0
    vector_names = None

    if vector_entry:
        f, root, raw = vector_entry
        findings = analyze_vector(root, raw, str(f), apply_fix=False)
        vector_names = collect_names(root)
        print_findings(findings, f"{f}  <vector>")
        if any(fi.level == "ERROR" for fi in findings):
            exit_code = 1

    if animated_entry:
        f, root, raw = animated_entry
        vector_stem = vector_entry[0].stem if vector_entry else None
        findings = analyze_animated_vector(root, raw, str(f), vector_names, vector_stem,
                                            animator_dir_for(f), apply_fix=False)
        print_findings(findings, f"{f}  <animated-vector>")
        if any(fi.level == "ERROR" for fi in findings):
            exit_code = 1

    for f, root in animator_entries:
        findings = analyze_animator(root, str(f), apply_fix=False)
        print_findings(findings, f"{f}  <{strip_ns(root.tag)}>")
        if any(fi.level == "ERROR" for fi in findings):
            exit_code = 1

    for f in parsed:
        if f not in recognized:
            print(f"\nAviso: {f} tiene raiz <{strip_ns(parsed[f][0].tag)}>, no se verifico "
                  f"(se reconocen vector, animated-vector, objectAnimator, set).", file=sys.stderr)

    return exit_code


def cmd_repair(args):
    parsed = {}
    for f in args.files:
        if not f.is_file():
            print(f"[ERROR] {f}: no existe el archivo.", file=sys.stderr)
            continue
        try:
            parsed[f] = list(_read_and_parse(f))
        except ET.ParseError as exc:
            print(f"[ERROR] {f}: no se pudo parsear como XML: {exc}", file=sys.stderr)

    if not parsed:
        print("Ningun archivo pudo leerse.", file=sys.stderr)
        return 1

    vector_entry = next(((f, parsed[f][0]) for f in parsed if strip_ns(parsed[f][0].tag) == "vector"), None)
    animated_entry = next(((f, parsed[f][0]) for f in parsed if strip_ns(parsed[f][0].tag) == "animated-vector"), None)

    unresolved = False
    vector_names = None

    if vector_entry:
        f, root = vector_entry
        findings = analyze_vector(root, parsed[f][1], str(f), apply_fix=True)
        vector_names = collect_names(root)
        print_findings(findings, f"{f}  <vector>")
        if any(fi.level == "ERROR" and not fi.fixed for fi in findings):
            unresolved = True

    if animated_entry:
        f, root = animated_entry
        vector_stem = vector_entry[0].stem if vector_entry else None
        findings = analyze_animated_vector(root, parsed[f][1], str(f), vector_names, vector_stem,
                                            animator_dir_for(f), apply_fix=True)
        print_findings(findings, f"{f}  <animated-vector>")
        if any(fi.level == "ERROR" and not fi.fixed for fi in findings):
            unresolved = True

    for f in parsed:
        root = parsed[f][0]
        if strip_ns(root.tag) in ("objectAnimator", "set"):
            findings = analyze_animator(root, str(f), apply_fix=True)
            print_findings(findings, f"{f}  <{strip_ns(root.tag)}>")
            if any(fi.level == "ERROR" and not fi.fixed for fi in findings):
                unresolved = True

    if args.write:
        print()
        for f in parsed:
            root, raw = parsed[f]
            backup = f.with_name(f.name + ".bak")
            backup.write_text(raw, encoding="utf-8")
            write_xml(root, f)
            print(f"  (respaldo del original en {backup})")
    else:
        print("\n(No se escribio nada a disco; vuelve a ejecutar con --write para aplicar las "
              "correcciones marcadas como [FIXED]. Se guardara una copia .bak del original.)")

    return 1 if unresolved else 0


def cmd_list(args):
    if not args.file.is_file():
        print(f"No existe el archivo: {args.file}", file=sys.stderr)
        return 1
    root, _raw = _read_and_parse(args.file)
    if strip_ns(root.tag) != "vector":
        print(f"Error: {args.file} no es un <vector> (raiz=<{strip_ns(root.tag)}>).", file=sys.stderr)
        return 1

    animator_map = {}
    if args.animated:
        aroot, _araw = _read_and_parse(args.animated)
        adir = animator_dir_for(args.animated)
        for target in aroot.findall("target"):
            tname = aget(target, "name")
            tanim = aget(target, "animation")
            if not tname:
                continue
            summary = tanim
            m = re.match(r"^@animator/(\w+)$", tanim or "")
            if m:
                candidate = adir / f"{m.group(1)}.xml"
                if candidate.is_file():
                    try:
                        anim_root, _ = _read_and_parse(candidate)
                        summary = summarize_animator(anim_root)
                    except ET.ParseError:
                        pass
            animator_map[tname] = summary

    entries = []

    def walk_list(elem, depth):
        for child in elem:
            ctag = strip_ns(child.tag)
            if ctag == "group":
                entries.append(("group", depth, child))
                walk_list(child, depth + 1)
            elif ctag in ("path", "clip-path"):
                entries.append((ctag, depth, child))

    walk_list(root, 0)

    if args.json:
        data = {
            "file": str(args.file),
            "width": aget(root, "width"),
            "height": aget(root, "height"),
            "viewportWidth": aget(root, "viewportWidth"),
            "viewportHeight": aget(root, "viewportHeight"),
            "items": [],
        }
        for kind, depth, elem in entries:
            item = {"kind": kind, "depth": depth, "name": aget(elem, "name")}
            if kind == "group":
                for k in ("pivotX", "pivotY", "rotation", "translateX", "translateY", "scaleX", "scaleY", "alpha"):
                    v = aget(elem, k)
                    if v is not None:
                        item[k] = v
                item["animation"] = animator_map.get(item["name"])
            else:
                d = aget(elem, "pathData") or ""
                item.update({
                    "pathDataLength": len(d),
                    "bbox": compute_bbox(d),
                    "fillColor": aget(elem, "fillColor"),
                    "fillAlpha": aget(elem, "fillAlpha"),
                    "fillType": aget(elem, "fillType"),
                    "strokeColor": aget(elem, "strokeColor"),
                    "strokeWidth": aget(elem, "strokeWidth"),
                    "strokeAlpha": aget(elem, "strokeAlpha"),
                    "animation": animator_map.get(item["name"]),
                })
            data["items"].append(item)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    print(f"{args.file}: vector {aget(root,'width')}x{aget(root,'height')}, "
          f"viewport {aget(root,'viewportWidth')}x{aget(root,'viewportHeight')}")
    if not entries:
        print("  (sin paths/groups)")
    for kind, depth, elem in entries:
        indent = "  " * (depth + 1)
        name = aget(elem, "name") or "(sin nombre)"
        if kind == "group":
            attrs = {k: aget(elem, k) for k in
                     ("pivotX", "pivotY", "rotation", "translateX", "translateY", "scaleX", "scaleY", "alpha")
                     if aget(elem, k) is not None}
            extra = " ".join(f"{k}={v}" for k, v in attrs.items())
            print(f"{indent}[group] {name}  {extra}")
            anim = animator_map.get(name)
            if anim:
                print(f"{indent}    animacion: {anim}")
        else:
            d = aget(elem, "pathData") or ""
            bits = []
            fill = aget(elem, "fillColor")
            if fill:
                alpha = aget(elem, "fillAlpha")
                bits.append(f"fill={fill}" + (f" fillAlpha={alpha}" if alpha else ""))
            stroke = aget(elem, "strokeColor")
            if stroke:
                bits.append(f"stroke={stroke} strokeWidth={aget(elem, 'strokeWidth')}")
            fill_type = aget(elem, "fillType")
            if fill_type:
                bits.append(f"fillType={fill_type}")
            print(f"{indent}[{kind}] {name}  {'  '.join(bits)}")
            print(f"{indent}    pathData: {len(d)} caracteres, bbox~={compute_bbox(d)}")
            anim = animator_map.get(name)
            if anim:
                print(f"{indent}    animacion: {anim}")
    return 0


def cmd_edit(args):
    if not args.file.is_file():
        print(f"No existe el archivo: {args.file}", file=sys.stderr)
        return 1
    root, raw = _read_and_parse(args.file)
    kind = strip_ns(root.tag)

    target_elem = root
    if args.target is not None:
        matches = [e for e in root.iter() if aget(e, "name") == args.target]
        if not matches:
            print(f"Error: no se encontro ningun elemento con android:name='{args.target}'.", file=sys.stderr)
            return 1
        if len(matches) > 1:
            print(f"Error: hay {len(matches)} elementos con android:name='{args.target}'; "
                  f"no se puede editar de forma ambigua.", file=sys.stderr)
            return 1
        target_elem = matches[0]
    elif args.property is not None:
        if kind != "set":
            print("Error: --property solo aplica cuando la raiz del archivo es <set>.", file=sys.stderr)
            return 1
        matches = [c for c in root if aget(c, "propertyName") == args.property]
        if not matches:
            print(f"Error: no se encontro ningun objectAnimator con propertyName='{args.property}'.", file=sys.stderr)
            return 1
        target_elem = matches[0]
    elif args.index is not None:
        if kind != "set":
            print("Error: --index solo aplica cuando la raiz del archivo es <set>.", file=sys.stderr)
            return 1
        children = list(root)
        if not (0 <= args.index < len(children)):
            print(f"Error: indice {args.index} fuera de rango (0..{len(children) - 1}).", file=sys.stderr)
            return 1
        target_elem = children[args.index]

    if not args.set:
        print("Nada que editar: pasa --set clave=valor (se puede repetir).", file=sys.stderr)
        return 1

    changes = []
    for item in args.set:
        if "=" not in item:
            print(f"Error: '--set {item}' debe tener la forma clave=valor.", file=sys.stderr)
            return 1
        key, value = item.split("=", 1)
        key = key.strip()
        old = aget(target_elem, key)
        aset(target_elem, key, value)
        changes.append((key, old, value))

    label = aget(target_elem, "name")
    header = f"<{strip_ns(target_elem.tag)} name='{label}'>" if label else f"<{strip_ns(target_elem.tag)}> (raiz)"
    print(f"Editando {header} en {args.file}")
    for key, old, new in changes:
        print(f"  android:{key}: {old!r} -> {new!r}")

    if args.write:
        backup = args.file.with_name(args.file.name + ".bak")
        backup.write_text(raw, encoding="utf-8")
        write_xml(root, args.file)
        print(f"  (respaldo del original en {backup})")
    else:
        print("\n(No se escribio nada a disco; vuelve a ejecutar con --write para aplicar los cambios.)")
    return 0


# --------------------------------------------------------------------------
# Menu interactivo
# --------------------------------------------------------------------------
# Atajos de atributos frecuentes por tipo de elemento, para no tener que
# escribir "clave=valor" de memoria en el menu interactivo.
ATTR_PRESETS = {
    "vector": [
        ("width", "ancho del drawable"), ("height", "alto del drawable"),
        ("viewportWidth", "ancho del viewport"), ("viewportHeight", "alto del viewport"),
    ],
    "group": [
        ("name", "nombre"), ("rotation", "rotacion (grados)"),
        ("pivotX", "pivote X"), ("pivotY", "pivote Y"),
        ("translateX", "traslacion X"), ("translateY", "traslacion Y"),
        ("scaleX", "escala X"), ("scaleY", "escala Y"), ("alpha", "opacidad (0-1)"),
    ],
    "path": [
        ("fillColor", "color de relleno"), ("fillAlpha", "opacidad de relleno (0-1)"),
        ("fillType", "regla de relleno"), ("strokeColor", "color de trazo"),
        ("strokeWidth", "ancho de trazo"), ("strokeAlpha", "opacidad de trazo (0-1)"),
        ("trimPathStart", "trimPathStart (0-1)"), ("trimPathEnd", "trimPathEnd (0-1)"),
        ("trimPathOffset", "trimPathOffset (0-1)"), ("name", "nombre"),
    ],
    "clip-path": [("name", "nombre")],
    "animated-vector": [("drawable", "drawable de referencia (@drawable/...)")],
    "target": [("name", "nombre del target"), ("animation", "animador de referencia (@animator/...)")],
    "objectAnimator": [
        ("propertyName", "propiedad a animar"), ("valueFrom", "valor inicial"),
        ("valueTo", "valor final"), ("valueType", "tipo de valor"),
        ("duration", "duracion (ms)"), ("startOffset", "desfase/delay (ms)"),
        ("repeatCount", "repeticiones"), ("repeatMode", "modo de repeticion"),
        ("interpolator", "interpolador"),
    ],
    "set": [("ordering", "orden de reproduccion de los hijos")],
}

# Atributos cuyo valor conviene elegir de una lista corta en vez de escribirlo.
CHOICE_ATTRS = {
    "fillType": ["nonzero", "evenOdd"],
    "valueType": ["floatType", "colorType", "pathType", "intType"],
    "repeatMode": ["restart", "reverse"],
    "ordering": ["together", "sequentially"],
    "propertyName": sorted(KNOWN_PROPERTY_NAMES),
    "repeatCount": ["infinite", "0", "1", "2", "3"],
    "interpolator": [
        "@android:anim/linear_interpolator",
        "@android:anim/accelerate_interpolator",
        "@android:anim/decelerate_interpolator",
        "@android:anim/accelerate_decelerate_interpolator",
        "@android:anim/anticipate_interpolator",
        "@android:anim/overshoot_interpolator",
        "@android:anim/anticipate_overshoot_interpolator",
        "@android:anim/bounce_interpolator",
        "@android:anim/cycle_interpolator",
    ],
}

COLOR_INPUT_ATTRS = {"fillColor", "strokeColor"}


def prompt(msg, default=""):
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{msg}{suffix}: ").strip()
    except EOFError:
        val = ""
    return val if val else default


def prompt_required(msg):
    while True:
        val = prompt(msg)
        if val:
            return val
        print("  (este dato es obligatorio)")


def prompt_int(msg, default):
    val = prompt(msg, str(default))
    try:
        return int(val)
    except ValueError:
        print(f"  Valor invalido, se usa {default}.")
        return default


def prompt_choice(msg, choices, default):
    val = prompt(f"{msg} ({'/'.join(choices)})", default)
    if val not in choices:
        print(f"  Opcion invalida, se usa '{default}'.")
        return default
    return val


def prompt_yes_no(msg, default=False):
    val = prompt(f"{msg} (s/n)", "s" if default else "n").lower()
    return val.startswith("s")


def menu_show_session(session):
    print("\nArchivos cargados:")
    print(f"  vector:          {session['vector'] or '(ninguno)'}")
    print(f"  animated-vector: {session['animated'] or '(ninguno)'}")


def menu_convert(session):
    print("\n-- Convertir SVG a AnimatedVectorDrawable --")
    svg_path = Path(prompt_required("Ruta del archivo SVG"))
    if not svg_path.is_file():
        print(f"No existe el archivo: {svg_path}")
        return
    name = prompt("Nombre base del recurso (vacio = nombre del archivo SVG)")
    out_dir = Path(prompt("Carpeta de salida", "."))
    animation = prompt_choice("Tipo de animacion", ["rotate", "fade", "draw"], "rotate")
    duration = prompt_int("Duracion en ms", 1000)
    stagger = prompt_int("Desfase entre trazos en ms (solo para 'draw')", 120)
    width_s = prompt("Ancho en dp (vacio = automatico del SVG)")
    height_s = prompt("Alto en dp (vacio = automatico del SVG)")
    args = argparse.Namespace(
        svg_file=svg_path, out_dir=out_dir, name=name or None, animation=animation,
        duration=duration, stagger=stagger,
        width=float(width_s) if width_s else None, height=float(height_s) if height_s else None,
    )
    try:
        if cmd_convert(args) == 0 and prompt_yes_no("Cargar los archivos generados en la sesion de trabajo?", True):
            base_name = sanitize_resource_name(name or svg_path.stem)
            session["vector"] = out_dir / "drawable" / f"{base_name}.xml"
            session["animated"] = out_dir / "drawable" / f"{base_name}_animated.xml"
            menu_show_session(session)
    except Exception as exc:
        print(f"Error: {exc}")


def menu_load(session):
    print("\n-- Cargar archivos de trabajo --")
    v = prompt("Ruta del archivo <vector>", str(session["vector"]) if session["vector"] else "")
    if v:
        p = Path(v)
        if p.is_file():
            session["vector"] = p
        else:
            print(f"  No existe: {p} (no se carga)")
    a = prompt("Ruta del archivo <animated-vector> (opcional)", str(session["animated"]) if session["animated"] else "")
    if a:
        p = Path(a)
        if p.is_file():
            session["animated"] = p
        else:
            print(f"  No existe: {p} (no se carga)")
    menu_show_session(session)


def menu_verify(session):
    print("\n-- Verificar --")
    files = [f for f in (session["vector"], session["animated"]) if f]
    if files:
        print("Se verificaran los archivos cargados: " + ", ".join(str(f) for f in files))
    extra = prompt("Archivo adicional a verificar (opcional, ej. un objectAnimator; vacio para omitir)")
    if extra:
        p = Path(extra)
        if p.is_file():
            files.append(p)
        else:
            print(f"  No existe: {p} (se omite)")
    if not files:
        print("No hay archivos para verificar. Carga archivos (opcion 2) o indica una ruta.")
        return
    try:
        cmd_verify(argparse.Namespace(files=files))
    except Exception as exc:
        print(f"Error: {exc}")


def menu_repair(session):
    print("\n-- Reparar --")
    files = [f for f in (session["vector"], session["animated"]) if f]
    if files:
        print("Se repararan los archivos cargados: " + ", ".join(str(f) for f in files))
    extra = prompt("Archivo adicional a reparar (opcional, ej. un objectAnimator; vacio para omitir)")
    if extra:
        p = Path(extra)
        if p.is_file():
            files.append(p)
        else:
            print(f"  No existe: {p} (se omite)")
    if not files:
        print("No hay archivos para reparar. Carga archivos (opcion 2) o indica una ruta.")
        return
    write = prompt_yes_no("Aplicar los cambios a disco ahora (--write)?", False)
    try:
        cmd_repair(argparse.Namespace(files=files, write=write))
    except Exception as exc:
        print(f"Error: {exc}")


def menu_list(session):
    print("\n-- Listar trazos --")
    vpath = session["vector"]
    if not vpath:
        v = prompt("Ruta del archivo <vector>")
        if not v:
            return
        vpath = Path(v)
    if not vpath.is_file():
        print(f"No existe: {vpath}")
        return
    as_json = prompt_yes_no("Mostrar salida en JSON?", False)
    try:
        cmd_list(argparse.Namespace(file=vpath, animated=session["animated"], json=as_json))
    except Exception as exc:
        print(f"Error: {exc}")


def prompt_value_for_attr(key, elem, file_path):
    """Pide el nuevo valor de `key`, con atajos segun el tipo de atributo:
    listas de opciones para enums, explorador de recursos @animator/, y
    normalizacion de color para fillColor/strokeColor. Devuelve el valor
    final como texto, o None si el usuario cancela."""
    current = aget(elem, key, "")

    if key == "animation" and file_path is not None:
        adir = animator_dir_for(file_path)
        xmls = sorted(adir.glob("*.xml")) if adir.is_dir() else []
        if xmls:
            print("    Animadores disponibles en " + str(adir) + ":")
            for i, x in enumerate(xmls, start=1):
                print(f"      {i}) {x.stem}")
            print("      0) escribir otra referencia")
            sel = prompt(f"    Elegir animador para '{key}'", "0")
            if sel.isdigit() and 1 <= int(sel) <= len(xmls):
                return f"@animator/{xmls[int(sel) - 1].stem}"

    if key in CHOICE_ATTRS:
        options = CHOICE_ATTRS[key]
        print(f"    Opciones para '{key}':")
        for i, opt in enumerate(options, start=1):
            marker = " (actual)" if opt == current else ""
            print(f"      {i}) {opt}{marker}")
        print("      0) escribir otro valor")
        sel = prompt(f"    Elegir valor para '{key}'", "0")
        if sel.isdigit() and 1 <= int(sel) <= len(options):
            return options[int(sel) - 1]
        return prompt(f"    Nuevo valor para '{key}'", current)

    if key in COLOR_INPUT_ATTRS:
        val = prompt(f"    Nuevo color para '{key}' (hex #RRGGBB/#AARRGGBB o nombre, ej. red)", current)
        if not val:
            return val
        if re.match(r"^#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$", val):
            return val
        fixed = fix_color(val)
        if fixed:
            return fixed
        print(f"    Aviso: '{val}' no se reconoce como color; se guarda tal cual.")
        return val

    return prompt(f"    Nuevo valor para '{key}'", current)


def menu_pick_attributes(elem, file_path):
    """Menu de atajos: muestra los atributos tipicos del elemento con su
    valor actual, numerados para elegirlos sin escribir 'clave=valor', mas
    una opcion 'L' para cualquier atributo libre (poder total). Devuelve
    la lista de cambios en formato ['clave=valor', ...]."""
    presets = ATTR_PRESETS.get(strip_ns(elem.tag), [])
    staged = []  # lista de (key, value) en orden, el ultimo valor gana

    while True:
        label = aget(elem, "name")
        header = f"<{strip_ns(elem.tag)}>" + (f" '{label}'" if label else "")
        print(f"\nAtributos rapidos para {header}:")
        for i, (key, desc) in enumerate(presets, start=1):
            current = next((v for k, v in reversed(staged) if k == key), aget(elem, key))
            shown = current if current is not None else "-"
            print(f"  {i:>2}) {key:<15} {desc:<32} (actual: {shown})")
        print("   L) atributo libre (clave=valor)")
        if staged:
            resumen = ", ".join(f"{k}={v}" for k, v in staged)
            print(f"  -> cambios preparados: {resumen}")
        choice = prompt("  Elegir (Enter para terminar)", "")
        if not choice:
            break
        if choice.lower() == "l":
            raw = prompt("  clave=valor")
            if not raw:
                continue
            if "=" not in raw:
                print("    Formato invalido, usa clave=valor.")
                continue
            key, value = (p.strip() for p in raw.split("=", 1))
            staged.append((key, value))
            continue
        if not choice.isdigit() or not (1 <= int(choice) <= len(presets)):
            print("  Opcion no reconocida.")
            continue
        key, _desc = presets[int(choice) - 1]
        value = prompt_value_for_attr(key, elem, file_path)
        staged.append((key, value))

    return [f"{k}={v}" for k, v in staged]


def menu_edit(session):
    print("\n-- Editar atributos --")
    print("  1) Vector cargado" + (f" ({session['vector']})" if session["vector"] else " (no cargado)"))
    print("  2) Animated-vector cargado" + (f" ({session['animated']})" if session["animated"] else " (no cargado)"))
    print("  3) Otro archivo (ruta libre, ej. un objectAnimator)")
    choice = prompt("Elegir", "3")
    if choice == "1" and session["vector"]:
        file_path = session["vector"]
    elif choice == "2" and session["animated"]:
        file_path = session["animated"]
    else:
        file_path = Path(prompt_required("Ruta del archivo a editar"))

    if not file_path.is_file():
        print(f"No existe: {file_path}")
        return
    try:
        root, _raw = _read_and_parse(file_path)
    except ET.ParseError as exc:
        print(f"No se pudo parsear: {exc}")
        return

    kind = strip_ns(root.tag)
    print(f"Raiz detectada: <{kind}>")

    target = property_name = index = None
    elem_for_presets = root
    if kind in ("vector", "animated-vector"):
        named = [(aget(e, "name"), e) for e in root.iter() if aget(e, "name")]
        if named:
            print("Elementos con android:name disponibles:")
            print(f"  0) (editar la raiz <{kind}>)")
            for i, (name, elem) in enumerate(named, start=1):
                print(f"  {i}) {strip_ns(elem.tag)} '{name}'")
            sel = prompt_int("Elegir elemento a editar", 0)
            if 1 <= sel <= len(named):
                target, elem_for_presets = named[sel - 1]
        else:
            print("(No hay elementos con android:name; se editara la raiz.)")
    elif kind == "set":
        children = list(root)
        print("Hijos del <set>:")
        print("  0) (editar la raiz <set>)")
        for i, c in enumerate(children, start=1):
            print(f"  {i}) {strip_ns(c.tag)} propertyName={aget(c, 'propertyName')}")
        sel = prompt_int("Elegir elemento a editar", 0)
        if 1 <= sel <= len(children):
            index = sel - 1
            elem_for_presets = children[index]

    sets = menu_pick_attributes(elem_for_presets, file_path)
    if not sets:
        print("Nada que editar.")
        return

    write = prompt_yes_no("Aplicar los cambios a disco ahora (--write)?", False)
    args = argparse.Namespace(file=file_path, target=target, property=property_name,
                               index=index, set=sets, write=write)
    try:
        cmd_edit(args)
    except Exception as exc:
        print(f"Error: {exc}")


def run_menu():
    session = {"vector": None, "animated": None}
    print("=== SVG Animator: menu interactivo ===")
    print("(Ctrl+C para salir en cualquier momento)")
    try:
        while True:
            menu_show_session(session)
            print("\n--- Menu principal ---")
            print("1) Convertir un SVG nuevo")
            print("2) Cargar archivo(s) vector / animated-vector para trabajar")
            print("3) Verificar archivo(s) cargado(s)")
            print("4) Reparar archivo(s) cargado(s)")
            print("5) Listar los trazos del vector cargado")
            print("6) Editar atributos (vector, animated-vector u otro archivo)")
            print("0) Salir")
            choice = prompt("\nOpcion", "0")
            if choice == "1":
                menu_convert(session)
            elif choice == "2":
                menu_load(session)
            elif choice == "3":
                menu_verify(session)
            elif choice == "4":
                menu_repair(session)
            elif choice == "5":
                menu_list(session)
            elif choice == "6":
                menu_edit(session)
            elif choice in ("0", "q", "salir"):
                print("Hasta luego.")
                break
            else:
                print("Opcion no reconocida.")
    except (KeyboardInterrupt, EOFError):
        print("\nHasta luego.")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="svg_animator.py",
        description="Convierte SVG en recursos AnimatedVectorDrawable de Android y permite "
                     "verificar, reparar, listar y editar esos recursos.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_convert = sub.add_parser("convert", help="Convierte un SVG en vector + animated-vector + animator XML.")
    p_convert.add_argument("svg_file", type=Path, help="Archivo SVG de entrada")
    p_convert.add_argument("-o", "--out-dir", type=Path, default=Path("."),
                            help="Carpeta raiz de salida (se crean drawable/ y animator/ dentro). Default: carpeta actual")
    p_convert.add_argument("--name", help="Nombre base del recurso (default: nombre del archivo SVG)")
    p_convert.add_argument("--animation", choices=["rotate", "fade", "draw"], default="rotate",
                            help="Tipo de animacion a generar (default: rotate)")
    p_convert.add_argument("--duration", type=int, default=1000, help="Duracion de la animacion en ms (default: 1000)")
    p_convert.add_argument("--stagger", type=int, default=120,
                            help="Retraso en ms entre el inicio de cada path para --animation draw (default: 120)")
    p_convert.add_argument("--width", type=float, help="Ancho del drawable en dp (default: ancho/viewBox del SVG)")
    p_convert.add_argument("--height", type=float, help="Alto del drawable en dp (default: alto/viewBox del SVG)")
    p_convert.set_defaults(func=cmd_convert)

    p_verify = sub.add_parser("verify", help="Verifica uno o mas XML (vector, animated-vector, objectAnimator/set).")
    p_verify.add_argument("files", nargs="+", type=Path,
                           help="Uno o mas archivos a verificar (p.ej. el vector y su animated-vector)")
    p_verify.set_defaults(func=cmd_verify)

    p_repair = sub.add_parser("repair", help="Repara los problemas detectables e imprime un reporte.")
    p_repair.add_argument("files", nargs="+", type=Path)
    p_repair.add_argument("--write", action="store_true",
                           help="Aplica las correcciones a disco (crea una copia .bak de cada original)")
    p_repair.set_defaults(func=cmd_repair)

    p_list = sub.add_parser("list", help="Lista los trazos (paths/groups) de un vector con sus detalles.")
    p_list.add_argument("file", type=Path, help="Archivo <vector> a inspeccionar")
    p_list.add_argument("--animated", type=Path,
                         help="animated-vector asociado, para mostrar que animacion usa cada trazo")
    p_list.add_argument("--json", action="store_true", help="Salida en JSON en lugar de tabla de texto")
    p_list.set_defaults(func=cmd_list)

    p_edit = sub.add_parser("edit", help="Edita atributos generales o de un trazo/animador especifico.")
    p_edit.add_argument("file", type=Path, help="Archivo XML a editar (vector, animated-vector u objectAnimator/set)")
    p_edit.add_argument("--target", help="android:name del path/group/target a editar (si se omite, se edita la raiz)")
    p_edit.add_argument("--property", dest="property", help="propertyName del objectAnimator a editar (solo si la raiz es <set>)")
    p_edit.add_argument("--index", type=int, help="indice 0-based del hijo a editar (solo si la raiz es <set>)")
    p_edit.add_argument("--set", action="append", metavar="CLAVE=VALOR",
                         help="Atributo android:* a modificar, ej: duration=800. Repetible.")
    p_edit.add_argument("--write", action="store_true",
                         help="Aplica los cambios a disco (crea una copia .bak del original)")
    p_edit.set_defaults(func=cmd_edit)

    p_menu = sub.add_parser("menu", help="Abre el menu interactivo para cargar y trabajar con los archivos.")
    p_menu.set_defaults(func=lambda args: run_menu())

    return parser


def main():
    if len(sys.argv) == 1:
        run_menu()
        return
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
