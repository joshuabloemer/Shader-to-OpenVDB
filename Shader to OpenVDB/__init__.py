"""
    Copyright (C) 2021  Joshua Blömer

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>
"""

import bpy
from bpy_extras.io_utils import (
    ExportHelper,
    path_reference_mode,
)
from bpy.props import (
    IntProperty,
    BoolProperty,
)
from mathutils import Vector
import math
from colorsys import rgb_to_hsv, hsv_to_rgb
import time
import sys

# broken in 2.91 and newer until openvdb api is natively supported in blender
from .pyopenvdb import pyopenvdb as vdb

bl_info = {
    "name": "Export Shader as OpenVDB",
    "author": "Joshua Blömer",
    "version": (1, 0, 0),
    "blender": (2, 90, 0),
    "location": "File > Export > OpenVDB (.vdb)",
    "description": "",
    "category": "Import-Export",
}


results = []
node_index = 0
node_index_list = []


def fract(x):
    return x - math.floor(x)


def safe_mod(a, b):
    return math.fmod(a, b) if b != 0 else 0

def safe_divide(a, b):
    return a/b if b != 0 else b

def vec_divide(a, b):
    return Vector([c/d if d != 0 else 0 for c, d in zip(a, b)])


def clamp(x, lower, upper):
    return max(lower, min(upper, x))


def update_progress(job_title, progress):
    length = 20
    block = int(round(length*progress))
    msg = "\r{0}: [{1}] {2}%".format(
        job_title, "#"*block + "-"*(length-block), round(progress*100, 2))
    if progress >= 1:
        msg += " DONE\r\n"
    sys.stdout.write(msg)
    sys.stdout.flush()


def get_nodes(node):
    global node_index

    nodes = [node.type+getattr(node, 'operation', '')+getattr(node,
                                                              'interpolation_type', ''), [], [], node]
    if node.type == 'VALUE':
        nodes[1].append(node.outputs[0].default_value)
    elif node.type == 'RGB':
        nodes[1].append(node.outputs[0].default_value)
    else:
        for input in node.inputs:
            if input.links:
                from_node = input.links[0].from_node
                i = list(from_node.outputs).index(input.links[0].from_socket)
                nodes[2].append(i)
                nodes[1].append(get_nodes(from_node))
            elif type(input.default_value) is float:
                nodes[1].append([input.default_value])
                nodes[2].append(0)
            else:
                nodes[1].append([Vector(input.default_value)])
                nodes[2].append(0)
    return nodes


def MATHWRAP(e, nodes, j, co):
    x = e(nodes[1][0], co)[j[0]]
    x_min = e(nodes[1][1], co)[j[1]]
    x_max = e(nodes[1][2], co)[j[2]]
    return (((x - x_min) % (x_max - x_min)) +
            (x_max - x_min)) % (x_max - x_min) + x_min


def MATHSNAP(e, nodes, j, co):
    x = e(nodes[1][0], co)[j[0]]
    return x-x % e(nodes[1][1], co)[j[1]]


def MATHCOMPARE(e, nodes, j, co):
    value1 = e(nodes[1][0], co)[j[0]]
    value2 = e(nodes[1][1], co)[j[1]]
    epsilon = e(nodes[1][2], co)[j[2]]
    return int(((value1 == value2) or (
        abs(value1 - value2) <= max(epsilon, 1e-5))))


def MATHSMOOTH_MIN(e, nodes, j, co):
    a = e(nodes[1][0], co)[j[0]]
    b = e(nodes[1][1], co)[j[1]]
    c = e(nodes[1][2], co)[j[2]]
    if c != 0:
        h = max(c-abs(a-b), 0)/c
        return min(a, b)-h*h*h*c*(1/6)
    else:
        return min(a, b)


def MATHSMOOTH_MAX(e, nodes, j, co):
    a = -e(nodes[1][0], co)[j[0]]
    b = -e(nodes[1][1], co)[j[1]]
    c = e(nodes[1][2], co)[j[2]]
    if c != 0:
        h = max(c-abs(a-b), 0)/c
        return -(min(a, b)-h*h*h*c*(1/6))
    else:
        return -min(a, b)


def MATHPINGPONG(e, nodes, j, co):
    a = e(nodes[1][0], co)[j[0]]
    b = e(nodes[1][1], co)[j[1]]
    return abs(fract((a - b) / (b * 2.0)) * b * 2.0 - b) if b != 0 else 0


def VECT_MATHWRAP(e, nodes, j, co):
    vec = []
    x_mem = e(nodes[1][0], co)[j[0]]
    x_min_mem = e(nodes[1][1], co)[j[1]]
    x_max_mem = e(nodes[1][2], co)[j[2]]
    for i in range(3):
        x = x_mem[i]
        x_min = x_min_mem[i]
        x_max = x_max_mem[i]
        vec.append(((safe_mod((x - x_min), (x_max - x_min))) +
                    safe_mod((x_max - x_min), (x_max - x_min)) + x_min))
    return Vector(vec)


def VECT_MATHSNAP(e, nodes, j, co):
    a = e(nodes[1][0], co)[j[0]]
    b = e(nodes[1][1], co)[j[1]]
    return Vector((math.floor(x) for x in vec_divide(a, b)))*b


def MAP_RANGELINEAR(e, nodes, j, co):
    v = e(nodes[1][0], co)[j[0]]
    fromMin = e(nodes[1][1], co)[j[1]]
    fromMax = e(nodes[1][2], co)[j[2]]
    toMin = e(nodes[1][3], co)[j[3]]
    toMax = e(nodes[1][4], co)[j[4]]
    f = (v - fromMin) / (fromMax - fromMin)
    return toMin + f * (toMax - toMin)


def MAP_RANGESTEPPED(e, nodes, j, co):
    v = e(nodes[1][0], co)[j[0]]
    fromMin = e(nodes[1][1], co)[j[1]]
    fromMax = e(nodes[1][2], co)[j[2]]
    toMin = e(nodes[1][3], co)[j[3]]
    toMax = e(nodes[1][4], co)[j[4]]
    steps = e(nodes[1][5], co)[j[5]]
    f = (v - fromMin) / (fromMax - fromMin)
    f = math.floor(f * (steps + 1)) / steps if steps > 0 else 0
    return toMin + f * (toMax - toMin)

def smoothstep(fromMax, fromMin, value):
    t = clamp((value - fromMax) / (fromMin - fromMax), 0, 1)
    return t**2 * (3 - 2 * t)

def MAP_RANGESMOOTHSTEP(e, nodes, j, co):
    v = e(nodes[1][0], co)[j[0]]
    fromMin = e(nodes[1][1], co)[j[1]]
    fromMax = e(nodes[1][2], co)[j[2]]
    toMin = e(nodes[1][3], co)[j[3]]
    toMax = e(nodes[1][4], co)[j[4]]
    factor = 1 - smoothstep(fromMax, fromMin, v) if (fromMin > fromMax) else smoothstep(fromMin, fromMax, v)
    return toMin + factor * (toMax - toMin)

def smootherstep(fromMax, fromMin, value):
    x = clamp(safe_divide((value - fromMax), (fromMin - fromMax)), 0, 1)
    return x*x*x*(x * (x * 6 - 15)+10)


def MAP_RANGESMOOTHERSTEP(e, nodes, j, co):
    v = e(nodes[1][0], co)[j[0]]
    fromMin = e(nodes[1][1], co)[j[1]]
    fromMax = e(nodes[1][2], co)[j[2]]
    toMin = e(nodes[1][3], co)[j[3]]
    toMax = e(nodes[1][4], co)[j[4]]
    factor = 1 - smootherstep(fromMax, fromMin, v) if (fromMin > fromMax) else smootherstep(fromMin, fromMax, v)
    return toMin + factor * (toMax - toMin)


def VALTORGB(e, nodes, j, co):  # Color ramp
    a = nodes[3].color_ramp.evaluate(e(nodes[1][0], co)[j[0]])
    return [a[:-1], a[-1]]


lambdable = {
    'MATHADD': lambda e, nodes, j, co: (e(nodes[1][0], co)[j[0]]+e(nodes[1][1], co)[j[1]]),
    'MATHSUBTRACT': ((lambda e, nodes, j, co: e(nodes[1][0], co)[j[0]]-e(nodes[1][1], co)[j[1]])),
    'MATHMULTIPLY': lambda e, nodes, j, co: (e(nodes[1][0], co)[j[0]] * e(nodes[1][1], co)[j[1]]),
    'MATHDIVIDE': lambda e, nodes, j, co: e(nodes[1][0], co)[j[0]]/e(nodes[1][1], co)[j[1]],
    'MATHSINH': lambda e, nodes, j, co: math.sinh(e(nodes[1][0], co)[j[0]]),
    'MATHCOSH': lambda e, nodes, j, co: math.cos(e(nodes[1][0], co)[j[0]]),
    'MATHTANH': lambda e, nodes, j, co: math.tanh(e(nodes[1][0], co)[j[0]]),
    'MATHRADIANS': lambda e, nodes, j, co: math.radians(e(nodes[1][0], co)[j[0]]),
    'MATHDEGREES': lambda e, nodes, j, co: math.degrees(e(nodes[1][0], co)[j[0]]),
    'MATHMULTIPLY_ADD': lambda e, nodes, j, co: (e(nodes[1][0], co)[j[0]]*e(nodes[1][1], co)[j[1]]+e(nodes[1][2], co)[j[2]]),
    'MATHPOWER': lambda e, nodes, j, co: (e(nodes[1][0], co)[j[0]]**e(nodes[1][1], co)[j[1]]),
    'MATHLOGARITHM': lambda e, nodes, j, co: (math.log(e(nodes[1][0], co)[j[0]], e(nodes[1][1], co)[j[1]])),
    'MATHSQRT': lambda e, nodes, j, co: (math.sqrt(e(nodes[1][0], co)[j[0]])),
    'MATHINVERSE_SQRT': lambda e, nodes, j, co: (1/math.sqrt(e(nodes[1][0], co)[j[0]])),
    'MATHABSOLUTE': lambda e, nodes, j, co: (abs(e(nodes[1][0], co)[j[0]])),
    'MATHEXPONENT': lambda e, nodes, j, co: (math.exp(e(nodes[1][0], co)[j[0]])),
    'MATHMINIMUM': lambda e, nodes, j, co: (min(e(nodes[1][0], co)[j[0]], e(nodes[1][1], co)[j[1]])),
    'MATHMAXIMUM': lambda e, nodes, j, co: (max(e(nodes[1][0], co)[j[0]], e(nodes[1][1], co)[j[1]])),
    'MATHLESS_THAN': lambda e, nodes, j, co: (int(e(nodes[1][0], co)[j[0]] < e(nodes[1][1], co)[j[1]])),
    'MATHGREATER_THAN': lambda e, nodes, j, co: (e(nodes[1][0], co)[j[0]] > e(nodes[1][1], co)[j[1]]),
    'MATHSIGN': lambda e, nodes, j, co: ((lambda x: (1, -1)[x < 0])(e(nodes[1][0], co)[j[0]])),
    'MATHROUND': lambda e, nodes, j, co: (round(e(nodes[1][0], co)[j[0]])),
    'MATHFLOOR': lambda e, nodes, j, co: (math.floor(e(nodes[1][0], co)[j[0]])),
    'MATHCEIL': lambda e, nodes, j, co: (math.ceil(e(nodes[1][0], co)[j[0]])),
    'MATHTRUNC': lambda e, nodes, j, co: (math.trunc(e(nodes[1][0], co)[j[0]])),
    'MATHFRACT': lambda e, nodes, j, co: (fract(e(nodes[1][0], co)[j[0]])),
    'MATHMODULO': lambda e, nodes, j, co: (safe_mod(e(nodes[1][0], co)[j[0]], e(nodes[1][1], co)[j[1]])),
    'MATHSINE': lambda e, nodes, j, co: (math.sin(e(nodes[1][0], co)[j[0]])),
    'MATHCOSINE': lambda e, nodes, j, co: (math.cos(e(nodes[1][0], co)[j[0]])),
    'MATHTANGENT': lambda e, nodes, j, co: (math.tan(e(nodes[1][0], co)[j[0]])),
    'MATHARCSINE': lambda e, nodes, j, co: (math.asin(e(nodes[1][0], co)[j[0]])),
    'MATHARCCOSINE': lambda e, nodes, j, co: (math.acos(e(nodes[1][0], co)[j[0]])),
    'MATHARCTANGENT': lambda e, nodes, j, co: (math.atan(e(nodes[1][0], co)[j[0]])),
    'MATHARCTAN2': lambda e, nodes, j, co: (math.atan2(e(nodes[1][0], co)[j[0]], e(nodes[1][1], co)[j[1]])),

    'VECT_MATHADD': lambda e, nodes, j, co: (e(nodes[1][0], co)[j[0]] + e(nodes[1][1], co)[j[1]]),
    'VECT_MATHSUBTRACT': lambda e, nodes, j, co: (e(nodes[1][0], co)[j[0]] - e(nodes[1][1], co)[j[1]]),
    'VECT_MATHMULTIPLY': lambda e, nodes, j, co: (e(nodes[1][0], co)[j[0]] * e(nodes[1][1], co)[j[1]]),
    'VECT_MATHDIVIDE': lambda e, nodes, j, co: vec_divide(e(nodes[1][0], co)[j[0]], e(nodes[1][1], co)[j[1]]),
    'VECT_MATHCROSS_PRODUCT': lambda e, nodes, j, co: (e(nodes[1][0], co)[j[0]].cross(e(nodes[1][1], co)[j[1]])),
    'VECT_MATHPROJECT': lambda e, nodes, j, co: (e(nodes[1][0], co)[j[0]].project(e(nodes[1][1], co)[j[1]])),
    'VECT_MATHDOT_PRODUCT': lambda e, nodes, j, co: [0, (e(nodes[1][0], co)[j[0]].dot(e(nodes[1][1], co)[j[1]]))],
    'VECT_MATHREFLECT': lambda e, nodes, j, co: (e(nodes[1][0], co)[j[0]].reflect(e(nodes[1][1], co)[j[1]])),
    'VECT_MATHDISTANCE': lambda e, nodes, j, co: [0, (e(nodes[1][0], co)[j[0]]-(e(nodes[1][1], co)[j[1]])).length],
    'VECT_MATHLENGTH': lambda e, nodes, j, co: ([0, e(nodes[1][0], co)[j[0]].length]),
    'VECT_MATHSCALE': lambda e, nodes, j, co: (e(nodes[1][0], co)[j[0]] * e(nodes[1][3], co)[j[3]]),
    'VECT_MATHNORMALIZE': lambda e, nodes, j, co: (e(nodes[1][0], co)[j[0]].normalize()),

    'VECT_MATHABSOLUTE': lambda e, nodes, j, co: (Vector(abs(x)for x in e(nodes[1][0], co)[j[0]])),
    'VECT_MATHMINIMUM': lambda e, nodes, j, co: (Vector((a if a < b else b) for a, b in zip(e(nodes[1][0], co)[j[0]], e(nodes[1][1], co)[j[1]]))),
    'VECT_MATHMAXIMUM': lambda e, nodes, j, co: (Vector((a if a > b else b) for a, b in zip(e(nodes[1][0], co)[j[0]], e(nodes[1][1], co)[j[1]]))),
    'VECT_MATHFLOOR': lambda e, nodes, j, co: (Vector(math.floor(x) for x in e(nodes[1][0], co)[j[0]])),
    'VECT_MATHCEIL': lambda e, nodes, j, co: (Vector(math.ceil(x) for x in e(nodes[1][0], co)[j[0]])),
    'VECT_MATHFRACTION': lambda e, nodes, j, co: (Vector(x % 1 for x in e(nodes[1][0], co)[j[0]])),
    'VECT_MATHMODULO': lambda e, nodes, j, co: (Vector(safe_mod(a, b) for a, b in zip(e(nodes[1][0], co)[j[0]], e(nodes[1][1], co)[j[1]]))),
    'VECT_MATHSINE': lambda e, nodes, j, co: (Vector(math.sin(x)for x in e(nodes[1][0], co)[j[0]])),
    'VECT_MATHCOSINE': lambda e, nodes, j, co: Vector(math.cos(x)for x in e(nodes[1][0], co)[j[0]]),
    'VECT_MATHTANGENT': lambda e, nodes, j, co: Vector(math.tan(x)for x in e(nodes[1][0], co)[j[0]]),

    'SEPXYZ': lambda e, nodes, j, co: (list(e(nodes[1][0], co)[j[0]])),
    'COMBXYZ': lambda e, nodes, j, co: (Vector((e(nodes[1][0], co)[j[0]], e(nodes[1][1], co)[j[1]], e(nodes[1][2], co)[j[2]]))),
    'SEPRGB': lambda e, nodes, j, co: (list(e(nodes[1][0], co)[j[0]])),
    'COMBRGB': lambda e, nodes, j, co: (Vector((e(nodes[1][0], co)[j[0]], e(nodes[1][1], co)[j[1]], e(nodes[1][2], co)[j[2]]))),
    'SEPHSV': lambda e, nodes, j, co: (list(rgb_to_hsv(*e(nodes[1][0], co)[j[0]][:-1]))),
    'COMBHSV': lambda e, nodes, j, co: (Vector((*hsv_to_rgb(e(nodes[1][0], co)[j[0]], e(nodes[1][1], co)[j[1]], e(nodes[1][2], co)[j[2]]),1))),
    'CLAMP': lambda e, nodes, j, co: (clamp(e(nodes[1][0], co)[j[0]], e(nodes[1][1], co)[j[1]], e(nodes[1][2], co)[j[2]])),
    'TEX_COORD': lambda e, nodes, j, co: ([Vector(((c+1)*0.5 for c in co)), 0, 0, Vector(co)]),
    'VALUE': lambda e, nodes, j, co: ([nodes[1][0]]),
    'RGB': lambda e, nodes, j, co: ([nodes[1][0]]),
    'REROUTE': lambda e, nodes, j, co: (e(nodes[1][0], co)[j[0]]),

    'MATHWRAP': MATHWRAP,
    'MATHSNAP': MATHSNAP,
    'MATHCOMPARE': MATHCOMPARE,
    'MATHSMOOTH_MIN': MATHSMOOTH_MIN,
    'MATHSMOOTH_MAX': MATHSMOOTH_MAX,
    'MATHPINGPONG': MATHPINGPONG,

    'VECT_MATHWRAP': VECT_MATHWRAP,
    'VECT_MATHSNAP': VECT_MATHSNAP,

    'MAP_RANGELINEAR': MAP_RANGELINEAR,
    'MAP_RANGESTEPPED': MAP_RANGESTEPPED,
    'MAP_RANGESMOOTHSTEP': MAP_RANGESMOOTHSTEP,
    'MAP_RANGESMOOTHERSTEP': MAP_RANGESMOOTHERSTEP,

    'VALTORGB': VALTORGB,


}


def evaluate(nodes, co):
    global results

    if type(nodes[0]) is not str:
        return [nodes[0]]
    a = node_index_list.index(nodes[3])

    if results[a] is not None:
        return results[a]

    return_val = lambdable[nodes[0]](evaluate, nodes, nodes[2], co)

    if type(return_val) is not list:
        return_val = [return_val]
    results[a] = return_val
    return return_val


class ExportVDB(bpy.types.Operator, ExportHelper):
    """Write a VDB file"""
    bl_idname = "export_scene.vdb"
    bl_label = "Export VDB"
    bl_options = {'UNDO', 'PRESET'}

    filename_ext = ".vdb"
    voxel_count: IntProperty(default=20)
    clamp_negative: BoolProperty(default=False, description='Clamps Negative Density Values to 0')

    @classmethod
    def poll(cls, context):
        return context.active_object and hasattr(context.active_object.data, 'materials')

    def draw(self, context):
        pass

    def execute(self, context):
        starttime = time.perf_counter()
        global results
        global node_index_list

        voxel_count = self.voxel_count
        c = context
        out = c.active_object.data.materials[0].node_tree.nodes.get(
            'Material Output')
        volume_in = out.inputs[1]
        volume_node = volume_in.links[0].from_node
        i = 0
        grid = vdb.FloatGrid()
        grid.name = 'density'
        accessor = grid.getAccessor()
        clamp_negative = self.clamp_negative
        if len(volume_node.inputs['Density'].links) != 0:
            node_index_list = list(
                context.object.data.materials[0].node_tree.nodes)
            first_node = volume_node.inputs['Density'].links[0].from_node
            nodes = get_nodes(first_node)

            for x in range(-voxel_count, voxel_count):
                for y in range(-voxel_count, voxel_count):
                    for z in range(-voxel_count, voxel_count):
                        results = [None for _ in range(
                            len(context.object.data.materials[0].node_tree.nodes))]
                        result = evaluate(
                            nodes, (x/voxel_count, y/voxel_count, z/voxel_count))[0]
                        accessor.setValueOn((x, y, z), result * (not clamp_negative) + (max(0,result) * clamp_negative))
                        i += 1
                        update_progress("Exporting", i/(voxel_count*2)**3)
        else:
            value = volume_node.inputs['Density'].default_value
            for x in range(-voxel_count, voxel_count):
                for y in range(-voxel_count, voxel_count):
                    for z in range(-voxel_count, voxel_count):
                        accessor.setValueOn((x, y, z), value * (not clamp_negative) + (max(0,value) * clamp_negative))
                        i += 1
                        update_progress("Exporting", i/(voxel_count*2)**3)

        grid.transform.scale((1/voxel_count, 1/voxel_count, 1/voxel_count))
        stoptime = time.perf_counter()
        print("Finished in ", stoptime-starttime, " seconds")
        vdb.write(self.filepath, grids=[grid])
        return {'FINISHED'}


class VDB_PT_export_main(bpy.types.Panel):
    bl_space_type = 'FILE_BROWSER'
    bl_region_type = 'TOOL_PROPS'
    bl_label = ""
    bl_parent_id = "FILE_PT_operator"
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        sfile = context.space_data
        operator = sfile.active_operator
        return operator.bl_idname == "EXPORT_SCENE_OT_vdb"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        sfile = context.space_data
        operator = sfile.active_operator

        col = layout.column(align=True)
        col.prop(operator, "voxel_count", text="Voxel Count:")
        col.prop(operator, "clamp_negative", text="Clamp Negative")


def menu_func_export(self, context):
    self.layout.operator(ExportVDB.bl_idname, text="OpenVDB (.vdb)")


classes = (
    ExportVDB,
    VDB_PT_export_main,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
