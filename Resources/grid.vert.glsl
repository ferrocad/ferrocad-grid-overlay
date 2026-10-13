#version 120
// Full-screen quad. The geometry in the scene graph is only a 4-vertex
// placeholder (tiny, centred on the origin, so it does not disturb the scene
// bounding box). Its corner sign picks the screen corner; positions are
// ignored. We output the world-space near/far points of each corner's view
// ray and let the fragment shader intersect them with the z=0 plane.
varying vec3 v_near;
varying vec3 v_far;

vec3 unproject(vec2 ndc, float z)
{
    vec4 e = gl_ProjectionMatrixInverse * vec4(ndc, z, 1.0);
    e /= e.w;
    return (gl_ModelViewMatrixInverse * e).xyz;
}

void main()
{
    vec2 ndc = sign(gl_Vertex.xy);
    v_near = unproject(ndc, -1.0);
    v_far  = unproject(ndc,  1.0);
    gl_Position = vec4(ndc, 0.0, 1.0);
}
