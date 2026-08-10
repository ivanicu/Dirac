struct CurvePoint {
    position_radius: vec4<f32>,
    tangent: vec4<f32>,
    normal: vec4<f32>,
}

struct CurveVertex {
    position: vec4<f32>,
    normal: vec4<f32>,
}

struct CurveParams {
    point_count: u32,
    sides: u32,
    first_point: u32,
    first_vertex: u32,
}

@group(0) @binding(0) var<storage, read> points: array<CurvePoint>;
@group(0) @binding(1) var<storage, read_write> vertices: array<CurveVertex>;
@group(0) @binding(2) var<uniform> params: CurveParams;

fn safe_normalize(value: vec3<f32>, fallback: vec3<f32>) -> vec3<f32> {
    let squared_length = dot(value, value);
    return select(fallback, value * inverseSqrt(max(squared_length, 1e-20)), squared_length > 1e-12);
}

// Semantic IDs stay in a u32/segment table, avoiding per-vertex duplication and interpolation ambiguity.
@compute @workgroup_size(64)
fn main(@builtin(global_invocation_id) invocation: vec3<u32>) {
    if (params.sides == 0u) {
        return;
    }
    let point_offset = invocation.x / params.sides;
    if (point_offset >= params.point_count) {
        return;
    }
    let side = invocation.x % params.sides;
    if (params.first_point >= arrayLength(&points) || point_offset >= arrayLength(&points) - params.first_point) {
        return;
    }
    let point_index = params.first_point + point_offset;
    if (params.first_vertex >= arrayLength(&vertices) || invocation.x >= arrayLength(&vertices) - params.first_vertex) {
        return;
    }
    let point = points[point_index];
    let tangent = safe_normalize(point.tangent.xyz, vec3<f32>(0.0, 0.0, 1.0));
    let orthogonal_normal = point.normal.xyz - tangent * dot(point.normal.xyz, tangent);
    var fallback_axis = vec3<f32>(1.0, 0.0, 0.0);
    if (abs(tangent.x) > 0.8) {
        fallback_axis = vec3<f32>(0.0, 1.0, 0.0);
    }
    let fallback_normal = safe_normalize(cross(fallback_axis, tangent), vec3<f32>(0.0, 1.0, 0.0));
    let normal = safe_normalize(orthogonal_normal, fallback_normal);
    let binormal = cross(tangent, normal);
    let angle = 6.28318530718 * f32(side) / f32(params.sides);
    let radial = normal * cos(angle) + binormal * sin(angle);
    let output_index = params.first_vertex + invocation.x;
    vertices[output_index].position = vec4<f32>(point.position_radius.xyz + radial * point.position_radius.w, 1.0);
    vertices[output_index].normal = vec4<f32>(radial, 0.0);
}
