/**
 * @license MIT
 * Self-contained WebGL Scene Player runtime page for Wallpaper Engine scenes.
 * Renders 2D layered scenes, post-processing shaders (reflection, waterwaves,
 * foliagesway, tint), and GPU/CPU particle systems (shooting stars, fireflies).
 *
 * Vendored verbatim from dsh-plugin-wallpaper-engine v0.6.8 (MIT,
 * github.com/elysia395/dsh-wallpaper-engine) as lib/scene-player.js.
 * Demiurge mounts it as an isolated same-origin blob: document iframe and feeds it a
 * blob: manifest (see ../scenePlayer.ts); the resource URLs it would otherwise fetch are
 * already rewritten to blob: URLs by ../decodeScenePackage.ts. Local changes
 * (layer parallax, puppet mesh drawing) are listed verbatim in NOTICE.md.
 */

export const WE_SCENE_PLAYER_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>Wallpaper Engine Scene Player</title>
<style>
  html, body {
    margin: 0;
    padding: 0;
    width: 100%;
    height: 100%;
    overflow: hidden;
    background: transparent;
  }
  canvas {
    display: block;
    width: 100%;
    height: 100%;
    position: absolute;
    inset: 0;
  }
</style>
</head>
<body>
<canvas id="canvas"></canvas>
<script>
(function() {
  'use strict';

  const canvas = document.getElementById('canvas');
  const gl = canvas.getContext('webgl', { alpha: true, depth: true, antialias: true, premultipliedAlpha: false }) ||
             canvas.getContext('experimental-webgl', { alpha: true, depth: true });
  if (!gl) return;

  let sceneData = null;
  let manifestOverridden = false;
  let isPaused = false;
  let contextLost = false;
  let fitMode = 'cover';
  let startTime = performance.now();
  let lastTime = performance.now();
  let animTime = 0;
  // 时钟/日期文字读的是墙上时间，但必须与 animTime 一样服从暂停：暂停后不再推进，
  // 画面里的时间停在暂停那一刻（WE 停掉脚本更新就是这个表现）。
  let wallClock = Date.now();
  let frameSeq = 0;
  let textureCache = new Map();
  // 视频纹理单独登记：它们必须每帧被重新采样，见 pumpVideoTextures。
  const videoTextures = new Set();
  let lastVideoPlayRetry = 0;
  let activeParticles = [];
  let mouseX = 0.5, mouseY = 0.5;
  let curRotX = 0, curRotY = 0;
  const puppetRuntimeCache = new Map();
  const poseCache = new Map();

  window.addEventListener('mousemove', (e) => {
    mouseX = e.clientX / window.innerWidth;
    mouseY = e.clientY / window.innerHeight;
  });

  // 3D Shaders
  const vs3D = \`
    attribute vec3 a_pos;
    attribute vec3 a_norm;
    attribute vec2 a_uv;
    uniform mat4 u_proj;
    uniform mat4 u_view;
    uniform mat4 u_model;
    uniform mat3 u_normMat;
    uniform float u_time;
    uniform int u_isJet;
    uniform int u_isAurora;
    uniform int u_isThunder;
    uniform int u_isBg;
    uniform int u_isNeonSun;
    varying vec3 v_norm;
    varying vec3 v_worldPos;
    varying vec2 v_uv;
    varying vec4 v_uv4;
    varying float v_alpha;
    void main() {
      v_uv = a_uv;
      v_uv4 = a_uv.xyxy;
      v_alpha = 1.0;
      vec3 pos = a_pos;
      // WE ricepodjet shader: flame pulse along the jet cone.
      if (u_isJet == 1) {
        float outside = step(0.5, a_uv.x);
        float pulseSpeed = 5.0 + outside * 10.0;
        float pulseAmount = 1.0 - a_uv.y;
        float pulseStrong = sin(u_time * pulseSpeed);
        pos.xy *= mix(1.0, pulseStrong * 0.05 + 1.0, pulseAmount);
        pos.z += pulseAmount * (cos(u_time * pulseSpeed) * 0.02 + 0.02);
        v_alpha = pulseStrong * 0.25 + 0.75;
      }
      // WE ricepodorbitalaurora: swaying curtain with scrolled multi-sample UVs.
      if (u_isAurora == 1) {
        pos.x += sin(0.1 * u_time + a_uv.x * 5.0) * 0.05;
        pos.y += sin(0.1 * u_time + a_uv.x * 3.0) * 0.02;
        v_uv4.xy = a_uv;
        v_uv4.x *= 5.7;
        v_uv4.x += fract(u_time * 0.05);
        v_uv4.zw = a_uv.xx;
        v_uv4.z *= 0.5;
        v_uv4.w *= 8.3;
        v_uv4.z += fract(u_time * 0.04);
        v_uv4.w -= fract(u_time * 0.03);
        v_alpha = smoothstep(0.0, 0.1, a_uv.x) * smoothstep(1.0, 0.9, a_uv.x) * 0.6;
      }
      // WE ricepodorbitalthunder: sparkle cells with drifting sample offsets.
      if (u_isThunder == 1) {
        v_uv4.xy = a_uv * 0.777;
        v_uv4.wz = a_uv * 0.3; // wz swizzle: w = x*0.3, z = y*0.3
        v_uv4.z += sin((1.7 + u_time) * 0.1);
        v_uv4.w += cos(u_time * 0.22);
      }
      // WE bg.vert: fullscreen background quad, position from UV directly.
      if (u_isBg == 1) {
        v_uv4 = vec4(a_uv + u_time * 0.03, a_uv.x * 2.0 - u_time * 0.0111, a_uv.y * 2.0 - u_time * 0.0111);
        gl_Position = vec4(a_uv * 2.0 - 1.0, 0.5, 1.0);
        return;
      }
      // WE neonsun.vert: procedural sun, uv remapped to a small disc space.
      if (u_isNeonSun == 1) {
        v_uv = (a_uv * 2.0 - 1.0) * 0.3;
      }
      vec4 worldPos = u_model * vec4(pos, 1.0);
      v_worldPos = worldPos.xyz;
      v_norm = normalize(u_normMat * a_norm);
      gl_Position = u_proj * u_view * worldPos;
    }
  \`;

  const fs3D = \`
    precision mediump float;
    varying vec3 v_norm;
    varying vec3 v_worldPos;
    varying vec2 v_uv;
    varying vec4 v_uv4;
    varying float v_alpha;
    uniform sampler2D u_tex;
    uniform int u_hasTex;
    uniform int u_isCarBody;
    uniform int u_isGlass;
    uniform int u_isDome;
    uniform int u_isShadow;
    uniform int u_isGrid;
    uniform int u_isSkybox;
    uniform int u_isSelfIllum;
    uniform highp int u_isJet;
    uniform highp int u_isAurora;
    uniform highp int u_isThunder;
    uniform highp int u_isBg;
    uniform highp int u_isNeonSun;
    uniform int u_gradFade;
    uniform int u_sceneStd;
    uniform vec3 u_jetPos[4];
    uniform int u_jetCount;
    uniform vec3 u_color;
    uniform vec3 u_paintColor;
    uniform vec3 u_stripeColor;
    uniform vec3 u_ambientColor;
    uniform vec3 u_cameraPos;
    uniform vec3 u_lightDir;
    uniform float u_specStrength;
    uniform float u_specPower;
    uniform highp float u_time;
    uniform int u_hasTint;
    uniform vec3 u_tint;
    uniform vec3 u_tint2;
    uniform sampler2D u_tex2;
    uniform sampler2D u_reflTex;
    uniform vec2 u_resolution;
    uniform int u_hasReflTex;
    void main() {
      // Skybox: textured background sphere (no lighting)
      if (u_isSkybox == 1) {
        vec3 col = u_hasTex == 1 ? texture2D(u_tex, v_uv).rgb : u_ambientColor * 0.5;
        gl_FragColor = vec4(col, 1.0);
        return;
      }
      // Dome: gradient sphere background (car scenes)
      if (u_isDome == 1) {
        vec3 tint = u_ambientColor;
        float h = normalize(v_worldPos).y * 0.5 + 0.5;
        vec3 col = mix(tint * 0.35, tint, h);
        gl_FragColor = vec4(col, 1.0);
        return;
      }
      // Shadow: smooth radial gradient under car
      if (u_isShadow == 1) {
        float d = length(v_worldPos.xz);
        float radial = 1.0 - smoothstep(0.0, 1.8, d);
        float yFade = pow(clamp(1.0 - v_uv.y, 0.0, 1.0), 1.5);
        float a = radial * yFade * 0.65;
        gl_FragColor = vec4(0.0, 0.0, 0.0, a);
        return;
      }
      // Grid floor: screen-space reflection from FBO
      if (u_isGrid == 1) {
        vec3 norm = normalize(v_norm);
        vec3 lightDir = normalize(u_lightDir);
        vec3 viewDir = normalize(u_cameraPos - v_worldPos);
        // Base grid color
        vec3 gridColor = u_ambientColor * 0.6;
        // Screen-space reflection from the mirrored-camera FBO
        vec3 reflColor = vec3(0.0);
        if (u_hasReflTex == 1) {
          vec2 screenUV = gl_FragCoord.xy / u_resolution;
          reflColor = texture2D(u_reflTex, screenUV).rgb;
        }
        // Distance-based fade for reflection
        float dist = length(v_worldPos.xz);
        float fade = 1.0 - smoothstep(0.0, 3.5, dist);
        // Fresnel for reflectivity at grazing angles
        float fresnel = 1.0 - max(dot(norm, viewDir), 0.0);
        fresnel = pow(fresnel, 2.0);
        // Specular highlight
        vec3 halfDir = normalize(lightDir + viewDir);
        float gridSpec = pow(max(dot(norm, halfDir), 0.0), 100.0) * 0.2;
        // Mix reflection with base color
        float reflStrength = fade * 0.55 + fresnel * 0.3;
        vec3 result = mix(gridColor, reflColor, reflStrength) + gridSpec;
        float alpha = 0.9 * fade + 0.1;
        gl_FragColor = vec4(result, alpha);
        return;
      }
      // Self-illuminated: emissive glow (jet engines, taillights with selfillum combo)
      if (u_isSelfIllum == 1) {
        vec3 col = u_hasTex == 1 ? texture2D(u_tex, v_uv).rgb : u_color;
        if (u_hasTint == 1) {
          // WE tinted-glow shaders (technoglow): pow falloff, scheme-color
          // tint, gentle pulse.
          float pulse = sin(u_time) * 0.25 + 0.75;
          col = col * col * u_tint * 3.0 * pulse;
          gl_FragColor = vec4(col, u_hasTex == 1 ? texture2D(u_tex, v_uv).a : 1.0);
        } else {
          gl_FragColor = vec4(col * 1.5, 1.0);
        }
        return;
      }
      // WE neonsun fragment: procedural retrowave sun (gradient disc, scanline
      // cutouts, glow halo). u_tint = colorsuntop, u_tint2 = colorsunbottom.
      if (u_isNeonSun == 1) {
        float sunSize = 0.05;
        float sunSizeSqrt = sqrt(sunSize);
        float blendSunColor = (v_uv.y + sunSize * 2.5) / sunSizeSqrt;
        vec4 colorSun = vec4(mix(u_tint, u_tint2, blendSunColor), 0.0);
        float sunRadius = dot(v_uv.xy, v_uv.xy);
        colorSun.a = 1.0 - step(0.05, sunRadius);
        float glowAlpha = pow(smoothstep(0.08, 0.045, sunRadius), 2.0);
        float barPos = v_uv.y + 0.1;
        float sunCutOut = 1.0 - clamp(smoothstep(0.0, 0.005, barPos) * smoothstep(1.0 - barPos * 9.0, 1.0 - barPos * 8.0, sin(barPos * 200.0 + u_time)), 0.0, 1.0);
        float sunCutOutSmooth = 1.0 - clamp(smoothstep(0.0, 0.05, barPos) * smoothstep(-1.0 - barPos * 8.0, 1.0 - barPos * 8.0, sin(barPos * 200.0 + u_time)), 0.0, 1.0);
        vec3 rgb = mix(u_tint2, colorSun.rgb, colorSun.a * sunCutOut);
        float sunA = max(glowAlpha * sunCutOutSmooth, colorSun.a * sunCutOut);
        gl_FragColor = vec4(rgb, sunA);
        return;
      }
      // WE ricepodjet fragment: flame texture fades along uv.y with pulse alpha.
      if (u_isJet == 1) {
        vec3 col = u_hasTex == 1 ? texture2D(u_tex, v_uv).rgb : u_color;
        col *= v_uv.y * v_alpha;
        gl_FragColor = vec4(col, 1.0);
        return;
      }
      // WE bg fragment: fullscreen tinted clouds + pattern background.
      if (u_isBg == 1) {
        float clouds = texture2D(u_tex, v_uv4.xy).a * texture2D(u_tex, v_uv4.zw).a * 1.4;
        clouds = clouds * clouds;
        float vignette = smoothstep(1.2, 0.0, length(v_uv - 0.5)) * 2.0;
        float pattern = texture2D(u_tex2, v_uv * 50.0).a * 0.1;
        pattern *= smoothstep(0.1, 0.7, length(v_uv - 0.5));
        vec3 albedo = mix(u_tint, u_tint2, v_uv.y * v_uv.y) * (clouds + pattern) * vignette;
        float bgAlpha = 1.0;
        if (u_gradFade == 1) {
          bgAlpha = smoothstep(0.2, 0.45, abs(v_uv.y - 0.5));
        }
        gl_FragColor = vec4(albedo, bgAlpha);
        return;
      }
      // WE ricepodorbitalaurora fragment: layered scrolling aurora curtains.
      if (u_isAurora == 1) {
        vec3 color = texture2D(u_tex, v_uv4.xy).rgb;
        vec3 color2 = texture2D(u_tex, v_uv4.wy).rgb;
        vec3 blend = texture2D(u_tex, v_uv4.zy).rgb;
        color = mix(color * color2, blend, blend.r);
        gl_FragColor = vec4(color, v_alpha);
        return;
      }
      // WE ricepodorbitalthunder fragment: sparkling blue cells.
      if (u_isThunder == 1) {
        float amt = texture2D(u_tex, v_uv4.xy).r;
        amt *= texture2D(u_tex, v_uv4.zw).r;
        vec3 color = mix(vec3(0.6, 0.5, 0.4), vec3(0.1, 0.3, 1.0), amt);
        gl_FragColor = vec4(color, amt);
        return;
      }

      vec4 baseColor = u_hasTex == 1 ? texture2D(u_tex, v_uv) : vec4(u_color, 1.0);
      float alpha = 1.0;

      // Car body paintwork: mix(paintColor, stripesColor, R) * G
      if (u_isCarBody == 1 && u_hasTex == 1) {
        vec3 bodyColor = mix(u_paintColor, u_stripeColor, baseColor.r) * baseColor.g;
        baseColor = vec4(bodyColor, 1.0);
      } else if (u_isGlass == 1) {
        alpha = u_hasTex == 1 ? baseColor.a * 0.6 : 0.3;
        baseColor.rgb = u_hasTex == 1 ? baseColor.rgb : vec3(0.15, 0.2, 0.28);
      }

      vec3 norm = normalize(v_norm);
      vec3 lightDir = normalize(u_lightDir);
      vec3 viewDir = normalize(u_cameraPos - v_worldPos);
      vec3 halfDir = normalize(lightDir + viewDir);

      if (u_sceneStd == 1) {
        // WE standard scene shading (shaders/ricepod.frag): warm key light,
        // strong planet sky-light from below, engine glow boost, gloss specular.
        float NdotL = max(dot(norm, lightDir), 0.0);
        vec3 lighting = NdotL * vec3(1.15, 1.1, 1.0);
        lighting += max(dot(norm, vec3(0.0, -3.0, 0.0)), 0.0) * vec3(0.4, 0.45, 0.55);
        float boostAmt = 0.0;
        for (int i = 0; i < 4; i++) {
          if (i < u_jetCount) {
            boostAmt += 1.0 - min(1.0, 2.0 * length(u_jetPos[i] - v_worldPos));
          }
        }
        vec3 boost = vec3(3.0, 1.2, 0.2) * boostAmt;
        float specBase = max(dot(halfDir, norm), 0.0);
        lighting += pow(specBase, 25.0 + 100.0 * smoothstep(0.3, 0.15, baseColor.r)) * 2.0;
        gl_FragColor = vec4(baseColor.rgb * (lighting + boost), alpha);
        return;
      }

      // Key light (squared falloff for car, linear for generic)
      float NdotL = max(dot(norm, lightDir), 0.0);
      float lighting = u_isCarBody == 1 ? NdotL * NdotL * 0.9 : NdotL * 1.1;

      // Fill light from opposite side
      vec3 fillDir = normalize(vec3(-lightDir.x, 0.3, -lightDir.z));
      float fillNdotL = max(dot(norm, fillDir), 0.0);
      lighting += fillNdotL * 0.25;

      // Sky light from below for generic scenes
      float skyLight = max(dot(norm, vec3(0.0, -1.0, 0.0)), 0.0);
      lighting += skyLight * 0.15;

      // Rim light
      float rim = 1.0 - max(dot(norm, viewDir), 0.0);
      rim = pow(rim, 3.0) * 0.3;

      // Specular
      float specBase = max(dot(halfDir, norm), 0.0);
      float spec = pow(specBase, u_specPower);
      if (u_isCarBody == 1) {
        spec = spec * smoothstep(0.0, 0.1, sin(spec * 12.0));
      }
      float specular = spec * u_specStrength;

      // Ricepod shader: specular += pow(specBase, 25 + 100 * smoothstep(0.3, 0.15, color.r)) * 2
      // Generic scenes get extra specular for metallic look
      if (u_isCarBody == 0 && u_isGlass == 0) {
        float extraSpec = pow(specBase, 25.0 + 100.0 * smoothstep(0.3, 0.15, baseColor.r)) * 2.0;
        specular += extraSpec * u_specStrength;
      }

      vec3 result = (u_ambientColor * 0.5 + lighting) * baseColor.rgb + specular + rim * u_ambientColor * 0.5;

      gl_FragColor = vec4(result, alpha);
    }
  \`;

  // Vertex shader for basic 2D quads
  const vsBasic = \`
    attribute vec2 a_pos;
    attribute vec2 a_uv;
    uniform mat4 u_proj;
    uniform mat4 u_model;
    uniform vec4 u_uvRect;
    varying vec2 v_uv;
    void main() {
      v_uv = u_uvRect.xy + a_uv * (u_uvRect.zw - u_uvRect.xy);
      gl_Position = u_proj * u_model * vec4(a_pos, 0.0, 1.0);
    }
  \`;

  // Fragment shader for standard textures
  const fsBasic = \`
    precision mediump float;
    varying vec2 v_uv;
    uniform sampler2D u_tex;
    uniform float u_alpha;
    uniform vec3 u_tint;
    uniform float u_bright;
    uniform float u_power;
    void main() {
      vec4 col = texture2D(u_tex, v_uv);
      col.rgb *= u_tint;
      col.rgb *= u_bright;
      col.rgb = pow(col.rgb, vec3(u_power));
      col.a *= u_alpha;
      gl_FragColor = col;
    }
  \`;

  // Fragment shader for water reflection
  const fsReflection = \`
    precision mediump float;
    varying vec2 v_uv;
    uniform sampler2D u_fbo;
    uniform sampler2D u_mask;
    uniform float u_time;
    uniform float u_alpha;
    // Scene-uv rect of the reflection quad: (leftU, topV, scaleU, scaleV);
    // (0,0,1,1) for a fullscreen layer. Scene v grows downward (0 at the top).
    uniform vec4 u_rect;
    // Data-driven water surface from the scene object (legacy default 0.65).
    uniform float u_waterLine;
    // Reflection sample window: start + puddleDepth * span (legacy 0.42/0.38).
    uniform vec2 u_reflectRange;
    void main() {
      float mask = texture2D(u_mask, v_uv).r;
      vec2 sceneUv = u_rect.xy + v_uv * u_rect.zw;
      if (mask < 0.05 || sceneUv.y < u_waterLine) {
        discard;
      }
      float puddleDepth = (sceneUv.y - u_waterLine) / max(1.0 - u_waterLine, 0.0001);
      vec2 uvReflect = vec2(sceneUv.x, u_reflectRange.x + puddleDepth * u_reflectRange.y);
      // water wave ripple perturbation
      float wave = sin(v_uv.y * 120.0 + u_time * 2.8) * 0.002 +
                   cos(v_uv.x * 90.0 + u_time * 1.9) * 0.0015;
      uvReflect.x += wave * mask;
      uvReflect.y += wave * mask;
      vec4 reflected = texture2D(u_fbo, clamp(uvReflect, 0.0, 1.0));
      reflected.rgb *= vec3(0.70, 0.75, 0.90);
      gl_FragColor = vec4(reflected.rgb, mask * u_alpha * 0.28);
    }
  \`;

  // WE flag shader (TINT combo): rippling cloth via two scrolling normal
  // samples, region colors remapped through texture channels.
  const fsFlag = \`
    precision mediump float;
    varying vec2 v_uv;
    uniform sampler2D u_tex;
    uniform sampler2D u_normal;
    uniform sampler2D u_cloth;
    uniform float u_time;
    uniform float u_speed;
    uniform float u_strength;
    uniform vec3 u_color1;
    uniform vec3 u_color2;
    uniform vec3 u_color3;
    void main() {
      vec2 nc1 = v_uv * vec2(1.0, 0.3) * 0.7;
      nc1.x -= u_time * u_speed;
      nc1.x -= ((0.5 - v_uv.x) * (1.0 - v_uv.y)) * 3.0;
      nc1.x += 2.0 * pow(v_uv.y - 0.1, 3.0) * pow(v_uv.x, 2.0);
      vec2 nc2 = v_uv * vec2(1.0, 0.7) * 0.3;
      nc2.x -= u_time * u_speed * 0.5;
      nc2.x -= ((1.0 - v_uv.x) * (1.0 - v_uv.y)) * 2.0;
      vec3 normal = texture2D(u_normal, nc1).rgb * 2.0 - 1.0;
      normal *= texture2D(u_normal, nc2).rgb * 2.0 - 1.0;
      normal = mix(vec3(0.0, 0.0, 1.0), normal, u_strength);
      normal = normalize(normal);
      vec2 baseCoords = v_uv + normal.xy * 0.02;
      vec3 albedo = texture2D(u_tex, baseCoords).rgb;
      float cloth = texture2D(u_cloth, baseCoords * 4.0).r;
      vec3 color = mix(u_color1, u_color2, albedo.r);
      color = mix(color, u_color3, albedo.g);
      color *= albedo.b * cloth;
      color += cloth * 0.1;
      float light = 0.2 + dot(vec3(0.707, 0.707, 0.0), normal) * 0.5 + 0.5;
      light += pow(light, 5.0) * 0.5;
      color *= light + light * clamp(cloth * 2.0 - 1.0, 0.0, 1.0);
      gl_FragColor = vec4(color, 1.0);
    }
  \`;

  // Fragment shader for particles
  const fsParticle = \`
    precision mediump float;
    varying vec2 v_uv;
    uniform sampler2D u_tex;
    uniform vec4 u_color;
    void main() {
      vec4 tex = texture2D(u_tex, v_uv);
      gl_FragColor = tex * u_color;
    }
  \`;

  // WE flowimage shader: 3 content layers cross-faded while their UVs drift
  // along the flow mask (deep_space nebula background).
  const fsFlow = \`
    precision mediump float;
    varying vec2 v_uv;
    uniform sampler2D u_mask;
    uniform sampler2D u_l1;
    uniform sampler2D u_l2;
    uniform sampler2D u_l3;
    uniform float u_time;
    uniform vec3 u_speeds;
    uniform float u_amp;
    uniform float u_bright;
    void main() {
      vec3 flowColors = texture2D(u_mask, v_uv).rgb;
      vec2 flowMask = (flowColors.rg - vec2(0.5, 0.5)) * 2.0;
      float c0 = fract(u_time * u_speeds.x);
      float c0b = fract(u_time * u_speeds.x + 0.5);
      float c1 = fract(u_time * u_speeds.y);
      float c1b = fract(u_time * u_speeds.y + 0.5);
      float c2 = fract(u_time * u_speeds.z);
      float c2b = fract(u_time * u_speeds.z + 0.5);
      float b0 = 2.0 * abs(c0 - 0.5);
      float b1 = 2.0 * abs(c1 - 0.5);
      float b2 = 2.0 * abs(c2 - 0.5);
      vec2 cuv = v_uv;
      vec4 albedo = mix(texture2D(u_l1, cuv + flowMask * u_amp * 0.1 * c0),
                        texture2D(u_l1, cuv + flowMask * u_amp * 0.1 * c0b), b0);
      vec4 s1 = mix(texture2D(u_l2, cuv + flowMask * u_amp * 0.1 * c1),
                    texture2D(u_l2, cuv + flowMask * u_amp * 0.1 * c1b), b1);
      albedo.rgb = mix(albedo.rgb, s1.rgb, s1.a);
      albedo.a = max(albedo.a, s1.a);
      vec4 s2 = mix(texture2D(u_l3, cuv + flowMask * u_amp * 0.1 * c2),
                    texture2D(u_l3, cuv + flowMask * u_amp * 0.1 * c2b), b2);
      albedo.rgb = mix(albedo.rgb, s2.rgb, s2.a);
      albedo.a = max(albedo.a, s2.a);
      albedo.rgb *= u_bright;
      gl_FragColor = albedo;
    }
  \`;

  // WE 的 waterflow 效果（本包＝白天/夜晚的云层流动）：单张内容图按流场遮罩的方向做
  // **像素级**扰动，相位图决定各点振动相位（取不到时是常量白＝全场同相）。u_amp 由 CPU 侧
  // 按 strength / 内容贴图宽换算成 UV，所以它是「占画面的比例」而不是 strength 原值。
  // 与 fsBasic 同色处理（tint/bright/power/alpha），否则开关一拨天空就跟着变明暗。
  const fsWaterflow = \`
    precision mediump float;
    varying vec2 v_uv;
    uniform sampler2D u_tex;
    uniform sampler2D u_mask;
    uniform sampler2D u_phase;
    uniform vec3 u_tint;
    uniform float u_bright;
    uniform float u_power;
    uniform float u_alpha;
    uniform float u_time;
    uniform float u_speed;
    uniform float u_amp;
    uniform float u_phasescale;
    void main() {
      vec2 dir = (texture2D(u_mask, v_uv).rg - vec2(0.5, 0.5)) * 2.0;
      float phase = texture2D(u_phase, v_uv).r;
      float wave = sin((phase * u_phasescale + u_time * u_speed) * 6.2831853);
      vec4 col = texture2D(u_tex, fract(v_uv + dir * (u_amp * wave)));
      col.rgb *= u_tint;
      col.rgb *= u_bright;
      col.rgb = pow(col.rgb, vec3(u_power));
      col.a *= u_alpha;
      gl_FragColor = col;
    }
  \`;

  function createShader(type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      console.error('we-scene-player shader compile failed:', gl.getShaderInfoLog(s));
    }
    return s;
  }

  function createProgram(vsSrc, fsSrc) {
    const p = gl.createProgram();
    gl.attachShader(p, createShader(gl.VERTEX_SHADER, vsSrc));
    gl.attachShader(p, createShader(gl.FRAGMENT_SHADER, fsSrc));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
      console.error('we-scene-player program link failed:', gl.getProgramInfoLog(p));
    }
    return p;
  }

  const progBasic = createProgram(vsBasic, fsBasic);
  const progReflection = createProgram(vsBasic, fsReflection);
  const progParticle = createProgram(vsBasic, fsParticle);
  const progFlow = createProgram(vsBasic, fsFlow);
  const progWaterflow = createProgram(vsBasic, fsWaterflow);
  const progFlag = createProgram(vsBasic, fsFlag);
  const prog3D = createProgram(vs3D, fs3D);

  // Camera-facing 3D billboard (sun sprites, 3D particle streaks). The quad is
  // offset in view space along two CPU-computed axes so streaks can stretch
  // along the velocity direction (WE spritetrail renderer).
  const vsSprite = \`
    attribute vec2 a_corner;
    attribute vec2 a_uv;
    uniform mat4 u_proj;
    uniform mat4 u_view;
    uniform vec3 u_center;
    uniform vec2 u_axisX;
    uniform vec2 u_axisY;
    varying vec2 v_uv;
    void main() {
      v_uv = a_uv;
      vec4 centerView = u_view * vec4(u_center, 1.0);
      gl_Position = u_proj * vec4(centerView.xy + a_corner.x * u_axisX + a_corner.y * u_axisY, centerView.zw);
    }
  \`;
  const fsSprite = \`
    precision mediump float;
    varying vec2 v_uv;
    uniform sampler2D u_tex;
    uniform int u_hasTex;
    uniform vec4 u_color;
    void main() {
      vec4 t = u_hasTex == 1 ? texture2D(u_tex, v_uv) : vec4(1.0);
      gl_FragColor = vec4(t.rgb * u_color.rgb, t.a * u_color.a);
    }
  \`;
  const progSprite = createProgram(vsSprite, fsSprite);

  // WE neongrid shader: procedural scrolling retrowave grid with fbm mountains.
  // Needs OES_standard_derivatives for the screen-space normal.
  const derivExt = gl.getExtension('OES_standard_derivatives');
  const vsNeonGrid = \`
    attribute vec3 a_pos;
    attribute vec2 a_uv;
    uniform mat4 u_proj;
    uniform mat4 u_view;
    uniform mat4 u_model;
    uniform float u_time;
    uniform float u_mountainScale;
    varying vec4 v_tc;
    varying vec4 v_vars;
    varying vec3 v_pos;
    float rand2(vec2 n) { return fract(sin(dot(n, vec2(12.9898, 4.1414))) * 43758.5453); }
    float noise2(vec2 p) {
      vec2 ip = floor(p);
      vec2 u = fract(p);
      u = u * u * (3.0 - 2.0 * u);
      float res = mix(mix(rand2(ip), rand2(ip + vec2(1.0, 0.0)), u.x), mix(rand2(ip + vec2(0.0, 1.0)), rand2(ip + vec2(1.0, 1.0)), u.x), u.y);
      return res * res;
    }
    float fbm(vec2 x) {
      float v = 0.0;
      float a = 0.5;
      vec2 shift = vec2(100.0);
      mat2 rot = mat2(cos(0.5), sin(0.5), -sin(0.5), cos(0.5));
      for (int i = 0; i < 5; ++i) {
        v += a * noise2(x);
        x = x * rot * 2.0 + shift;
        a *= 0.5;
      }
      return v;
    }
    void main() {
      v_vars = vec4(0.0);
      float speed = u_time * 2.0;
      vec3 localPos = a_pos;
      vec2 gridPos = floor(a_uv * 50.0 + vec2(0.0, speed));
      float dampenDistance = abs(a_uv.x * 2.0 - 1.0);
      float fallOffSides = pow(1.05 - dampenDistance, 0.5);
      float fallOffCenter = (0.2 + 0.8 * pow(dampenDistance, 2.0));
      float speedFrac = fract(speed) / 50.0;
      v_vars.x = a_uv.y - speedFrac;
      float dampenY = a_uv.y - speedFrac;
      float clipCenter = clamp(0.8 - dampenDistance, 0.0, 1.0);
      float offsetY = max(0.0, fbm(gridPos * 0.1) * 2.0 - clipCenter) * fallOffCenter * u_mountainScale;
      float maskUVSmoothing = step(0.005, offsetY);
      offsetY = offsetY * fallOffSides * dampenY + pow(dampenDistance, 2.0) * 0.02;
      localPos.z -= speedFrac * 2.0;
      localPos.y += offsetY;
      vec4 worldPos = u_model * vec4(localPos, 1.0);
      v_pos = worldPos.xyz;
      gl_Position = u_proj * u_view * worldPos;
      v_tc.xy = a_uv;
      v_tc.zw = a_uv * 50.0;
      float dampenUVSmoothing = clamp(abs(a_uv.x - 0.5) * 2.0 + maskUVSmoothing, 0.0, 1.0);
      v_vars.yz = vec2(0.45) - v_tc.y * vec2(0.05, 0.75 - dampenUVSmoothing * 0.7);
    }
  \`;
  const fsNeonGrid = (derivExt ? '#extension GL_OES_standard_derivatives : enable\\n' : '') + \`
    precision mediump float;
    varying vec4 v_tc;
    varying vec4 v_vars;
    varying vec3 v_pos;
    uniform vec3 u_gridNear;
    uniform vec3 u_gridFar;
    uniform vec3 u_gridBg;
    void main() {
      vec3 n = vec3(0.0, 1.0, 0.0);
      #ifdef GL_OES_standard_derivatives
      vec3 dx = dFdx(v_pos);
      vec3 dy = dFdy(v_pos);
      n = normalize(cross(dy, dx));
      #endif
      vec3 lightDir = normalize(vec3(0.0, -0.15, -2.0) - v_pos);
      vec2 grid = abs(fract(v_tc.zw) - 0.5);
      vec2 gridBlend = smoothstep(v_vars.yz, vec2(0.5), grid);
      float gridAlpha = gridBlend.x + gridBlend.y;
      gridBlend = smoothstep(vec2(0.0), vec2(1.0), grid);
      gridAlpha += (gridBlend.x + gridBlend.y) * clamp(0.3 - v_tc.y, 0.0, 1.0);
      float alphaDistanceFade = smoothstep(1.0, 0.9, v_vars.x);
      float colorDistanceBlend = pow(v_tc.y, 0.8);
      float shadingNear = dot(vec3(0.0, 0.0, 1.0), n);
      float shadingFar = dot(lightDir, n);
      vec3 shadingColor = clamp(shadingNear, 0.0, 1.0) * u_gridNear * (1.0 - colorDistanceBlend)
                        + clamp(shadingFar, 0.0, 1.0) * u_gridFar;
      vec3 colorGrid = u_gridBg + shadingColor;
      vec3 resultColor = mix(colorGrid, mix(u_gridNear, u_gridFar, colorDistanceBlend), gridAlpha * alphaDistanceFade);
      gl_FragColor = vec4(resultColor, alphaDistanceFade);
    }
  \`;
  const progNeonGrid = createProgram(vsNeonGrid, fsNeonGrid);

  function drawNeonGrid(model, mesh, proj, view, elapsed) {
    const uc = mesh.userColors || {};
    const un = mesh.userNums || {};
    gl.useProgram(progNeonGrid);
    gl.uniformMatrix4fv(gl.getUniformLocation(progNeonGrid, 'u_proj'), false, proj);
    gl.uniformMatrix4fv(gl.getUniformLocation(progNeonGrid, 'u_view'), false, view);
    gl.uniformMatrix4fv(gl.getUniformLocation(progNeonGrid, 'u_model'), false, mat4Transform3D(model.origin, model.angles, model.scale));
    gl.uniform1f(gl.getUniformLocation(progNeonGrid, 'u_time'), elapsed);
    gl.uniform1f(gl.getUniformLocation(progNeonGrid, 'u_mountainScale'), un.mountainscale != null ? un.mountainscale : 1);
    const near = uc.gridnear || [1, 0, 0.2];
    const far = uc.gridfar || [0, 0, 1];
    const bgc = uc.gridbackground || [0.1, 0, 0.1];
    gl.uniform3f(gl.getUniformLocation(progNeonGrid, 'u_gridNear'), near[0], near[1], near[2]);
    gl.uniform3f(gl.getUniformLocation(progNeonGrid, 'u_gridFar'), far[0], far[1], far[2]);
    gl.uniform3f(gl.getUniformLocation(progNeonGrid, 'u_gridBg'), bgc[0], bgc[1], bgc[2]);
    const gpu = getGpuMesh(mesh);
    const gPos = gl.getAttribLocation(progNeonGrid, 'a_pos');
    const gUv = gl.getAttribLocation(progNeonGrid, 'a_uv');
    gl.enableVertexAttribArray(gPos);
    gl.enableVertexAttribArray(gUv);
    gl.bindBuffer(gl.ARRAY_BUFFER, gpu.posBuf);
    gl.vertexAttribPointer(gPos, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, gpu.uvBuf);
    gl.vertexAttribPointer(gUv, 2, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gpu.idxBuf);
    gl.drawElements(gl.TRIANGLES, gpu.iCount, gpu.idxType, 0);
  }

  // WE cloudsbg shader: fullscreen scrolling clouds + horizon glow.
  const vsCloudsBg = \`
    attribute vec2 a_corner;
    attribute vec2 a_uv;
    uniform float u_time;
    uniform float u_aspect;
    varying vec2 v_uv;
    varying vec4 v_tcClouds;
    void main() {
      gl_Position = vec4(a_corner * 2.0, 0.0, 1.0);
      v_uv = a_uv;
      v_tcClouds.xy = (a_uv + u_time * 0.0007) * vec2(1.1, 1.1);
      v_tcClouds.zw = (a_uv - u_time * 0.0011) * vec2(0.7, 0.7);
      v_tcClouds.xz *= u_aspect;
      v_tcClouds.zw = vec2(-v_tcClouds.w, v_tcClouds.z);
    }
  \`;
  const fsCloudsBg = \`
    precision mediump float;
    varying vec2 v_uv;
    varying vec4 v_tcClouds;
    uniform sampler2D u_tex;
    uniform int u_hasTex;
    uniform vec3 u_color1;
    uniform vec3 u_colorHorizon;
    void main() {
      float cloud0 = u_hasTex == 1 ? texture2D(u_tex, v_tcClouds.xy).r : 0.0;
      float cloud1 = u_hasTex == 1 ? texture2D(u_tex, v_tcClouds.zw).r : 0.0;
      float cloudBlend = cloud0 * cloud1;
      vec3 albedo = u_color1 * cloudBlend;
      albedo += (u_color1 * 0.5 + albedo) * pow(smoothstep(0.5, 0.0, v_uv.y), 2.0) * 2.0;
      float horizonBend = 1.0 - cos(clamp(v_uv.x * 2.0 - 0.5, 0.0, 1.0) * 2.0 * 3.14159265);
      vec2 horizonDelta = (v_uv - vec2(0.5, 0.6)) * vec2(0.5, 1.5 - horizonBend * 0.3);
      albedo += u_colorHorizon * pow(smoothstep(0.5, 0.0, length(horizonDelta)), 2.0) * 2.0;
      gl_FragColor = vec4(albedo, 1.0);
    }
  \`;
  const progCloudsBg = createProgram(vsCloudsBg, fsCloudsBg);

  // Demiurge 改动②：层内视差。WE 的 parallaxDepth 是"该层相对画布的位移系数"（vec2，x/y 分轴）。
  // sceneW/sceneH 是 renderFrame 的局部量，因此由调用点显式传入。
  // WE 的相机视差是场景级开关：general.cameraparallax=false 时鼠标必须完全
  // 不影响画面（3346715292 就是这种"纯自动播放"壁纸）。
  const PARALLAX_RATIO = 0.06; // 满深度时的最大位移，占画布宽/高的比例
  function cameraParallaxEnabled() {
    return !sceneData || sceneData.cameraParallax !== false;
  }
  function layerOrigin(layer, sceneW, sceneH) {
    const d = layer.depth;
    if (!cameraParallaxEnabled() || !d || (!d[0] && !d[1])) return [layer.x, layer.y];
    return [ layer.x + (mouseX - 0.5) * d[0] * PARALLAX_RATIO * sceneW,
             layer.y + (mouseY - 0.5) * d[1] * PARALLAX_RATIO * sceneH ];
  }

  function drawCloudsBgLayer(layer, elapsed, width, height) {
    gl.useProgram(progCloudsBg);
    // A previous program may have left other vertex attribute arrays enabled but
    // unbound; disable them all so this draw never hits "no buffer is bound to
    // enabled attribute".
    const _maxAttribs = gl.getParameter(gl.MAX_VERTEX_ATTRIBS);
    for (let i = 0; i < _maxAttribs; i++) gl.disableVertexAttribArray(i);
    gl.bindBuffer(gl.ARRAY_BUFFER, spriteBuf);
    const cPos = gl.getAttribLocation(progCloudsBg, 'a_corner');
    const cUv = gl.getAttribLocation(progCloudsBg, 'a_uv');
    gl.enableVertexAttribArray(cPos);
    gl.enableVertexAttribArray(cUv);
    gl.vertexAttribPointer(cPos, 2, gl.FLOAT, false, 16, 0);
    gl.vertexAttribPointer(cUv, 2, gl.FLOAT, false, 16, 8);
    gl.uniform1f(gl.getUniformLocation(progCloudsBg, 'u_time'), elapsed);
    gl.uniform1f(gl.getUniformLocation(progCloudsBg, 'u_aspect'), width / Math.max(height, 1));
    const uc = layer.userColors || {};
    const c1 = uc.clouds || [0.05, 0.15, 0.4];
    const ch = uc.horizon || [0.05, 0.15, 0.4];
    gl.uniform3f(gl.getUniformLocation(progCloudsBg, 'u_color1'), c1[0], c1[1], c1[2]);
    gl.uniform3f(gl.getUniformLocation(progCloudsBg, 'u_colorHorizon'), ch[0], ch[1], ch[2]);
    if (layer.texUrl) {
      const texRec = loadTexture(layer.texUrl, true);
      if (texRec.loaded) {
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, texRec.texture);
        gl.uniform1i(gl.getUniformLocation(progCloudsBg, 'u_tex'), 0);
        gl.uniform1i(gl.getUniformLocation(progCloudsBg, 'u_hasTex'), 1);
      } else {
        gl.uniform1i(gl.getUniformLocation(progCloudsBg, 'u_hasTex'), 0);
      }
    } else {
      gl.uniform1i(gl.getUniformLocation(progCloudsBg, 'u_hasTex'), 0);
    }
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }
  const spriteBuf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, spriteBuf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
    -0.5, -0.5, 0.0, 0.0,
     0.5, -0.5, 1.0, 0.0,
    -0.5,  0.5, 0.0, 1.0,
     0.5,  0.5, 1.0, 1.0,
  ]), gl.STATIC_DRAW);

  function drawBillboard(center, axisX, axisY, texUrl, color, proj, view) {
    gl.useProgram(progSprite);
    gl.uniformMatrix4fv(gl.getUniformLocation(progSprite, 'u_proj'), false, proj);
    gl.uniformMatrix4fv(gl.getUniformLocation(progSprite, 'u_view'), false, view);
    gl.bindBuffer(gl.ARRAY_BUFFER, spriteBuf);
    const cPos = gl.getAttribLocation(progSprite, 'a_corner');
    const cUv = gl.getAttribLocation(progSprite, 'a_uv');
    gl.enableVertexAttribArray(cPos);
    gl.enableVertexAttribArray(cUv);
    gl.vertexAttribPointer(cPos, 2, gl.FLOAT, false, 16, 0);
    gl.vertexAttribPointer(cUv, 2, gl.FLOAT, false, 16, 8);
    gl.uniform3f(gl.getUniformLocation(progSprite, 'u_center'), center[0], center[1], center[2]);
    gl.uniform2f(gl.getUniformLocation(progSprite, 'u_axisX'), axisX[0], axisX[1]);
    gl.uniform2f(gl.getUniformLocation(progSprite, 'u_axisY'), axisY[0], axisY[1]);
    gl.uniform4f(gl.getUniformLocation(progSprite, 'u_color'), color[0], color[1], color[2], color[3]);
    if (texUrl) {
      const texRec = loadTexture(texUrl);
      if (texRec.loaded) {
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, texRec.texture);
        gl.uniform1i(gl.getUniformLocation(progSprite, 'u_tex'), 0);
        gl.uniform1i(gl.getUniformLocation(progSprite, 'u_hasTex'), 1);
      } else {
        gl.uniform1i(gl.getUniformLocation(progSprite, 'u_hasTex'), 0);
      }
    } else {
      gl.uniform1i(gl.getUniformLocation(progSprite, 'u_hasTex'), 0);
    }
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  // 3D particle system state, seeded from manifest.particles3d
  const particles3dState = new Map();
  function getParticles3d(sys) {
    if (!particles3dState.has(sys)) particles3dState.set(sys, { list: [], acc: 0 });
    return particles3dState.get(sys);
  }
  function updateParticles3d(sys, dt) {
    const st = getParticles3d(sys);
    st.acc += sys.rate * dt;
    while (st.acc >= 1 && st.list.length < sys.maxCount) {
      st.acc -= 1;
      // Random point on a sphere shell around the emitter origin.
      const th = Math.random() * Math.PI * 2;
      const ph = Math.acos(2 * Math.random() - 1);
      const r = sys.distMin + Math.random() * (sys.distMax - sys.distMin);
      const lerp = (a, b) => a + Math.random() * (b - a);
      st.list.push({
        x: sys.origin[0] + r * Math.sin(ph) * Math.cos(th),
        y: sys.origin[1] + r * Math.sin(ph) * Math.sin(th),
        z: sys.origin[2] + r * Math.cos(ph),
        vx: lerp(sys.velMin[0], sys.velMax[0]),
        vy: lerp(sys.velMin[1], sys.velMax[1]),
        vz: lerp(sys.velMin[2], sys.velMax[2]),
        size: lerp(sys.sizeMin, sys.sizeMax),
        life: 0,
        maxLife: lerp(sys.lifeMin, sys.lifeMax),
        color: [lerp(sys.colorMin[0], sys.colorMax[0]), lerp(sys.colorMin[1], sys.colorMax[1]), lerp(sys.colorMin[2], sys.colorMax[2])],
      });
    }
    st.acc = Math.min(st.acc, 4);
    for (let i = st.list.length - 1; i >= 0; i--) {
      const p = st.list[i];
      p.life += dt;
      if (p.life >= p.maxLife) { st.list.splice(i, 1); continue; }
      p.x += p.vx * dt; p.y += p.vy * dt; p.z += p.vz * dt;
    }
  }

  // WE 粒子系统的 2D 通道（雾 / 光束 / 鸟 / 星星）。清单里同一份 particles3d 现在带齐
  // 2D 语义：逐轴尺寸与扩散、混合模式、逐粒子 alpha、旋转、淡入淡出、闪烁、吸引点、
  // 序列帧。3D 通道继续用它自己的 updateParticles3d，两边状态分开，同一份参数不会被
  // 两条链互相踩。
  // 状态按系统对象存：改一次属性清单就整体重建，sys 是新对象 ⇒ 用 Map 会把每份旧清单的
  // 键永久留住（滑块每拖一格泄漏一组粒子），WeakMap 让旧状态随旧清单一起回收。
  const particles2dState = new WeakMap();
  let particles2dSteps = 0;
  const nz = (v, d) => (typeof v === 'number' && Number.isFinite(v) ? v : d);
  function getParticles2d(sys) {
    let st = particles2dState.get(sys);
    if (!st) {
      st = { list: [], acc: 0, warm: false };
      particles2dState.set(sys, st);
    }
    return st;
  }
  function spawnParticle2d(sys) {
    const rnd = (a, b) => a + Math.random() * (b - a);
    const dirs = sys.directions || [1, 1, 1];
    let ox;
    let oy;
    if (sys.box) {
      // boxrandom：distancemax 是各轴半宽，均匀取点。
      ox = (Math.random() * 2 - 1) * Math.abs(sys.box[0]);
      oy = (Math.random() * 2 - 1) * Math.abs(sys.box[1]);
    } else {
      // sphererandom：半径壳上取点，directions 当各轴比例（官方雾预设靠它把球压成扁带），
      // 半径与精灵尺寸同源——都是对象局部空间，一起乘 sizeScale 映射到场景空间。
      const th = Math.random() * Math.PI * 2;
      const lo = nz(sys.distMin, 0);
      const hi = Math.max(lo, nz(sys.distMax, 0));
      const r = lo + Math.random() * (hi - lo);
      const sc = sys.sizeScale || [1, 1];
      ox = Math.cos(th) * r * nz(sc[0], 1) * nz(dirs[0], 1);
      oy = Math.sin(th) * r * nz(sc[1], 1) * nz(dirs[1], 1);
    }
    const vmin = sys.velMin || [0, 0];
    const vmax = sys.velMax || [0, 0];
    const cmin = sys.colorMin || [1, 1, 1];
    const cmax = sys.colorMax || [1, 1, 1];
    const life = Math.max(0.1, rnd(nz(sys.lifeMin, 2), nz(sys.lifeMax, 4)));
    return {
      x: nz(sys.origin && sys.origin[0], 0) + ox,
      y: nz(sys.origin && sys.origin[1], 0) + oy,
      vx: rnd(vmin[0], vmax[0]),
      vy: rnd(vmin[1], vmax[1]),
      size: rnd(nz(sys.sizeMin, 32), nz(sys.sizeMax, 32)),
      life: 0,
      maxLife: life,
      color: [rnd(cmin[0], cmax[0]), rnd(cmin[1], cmax[1]), rnd(cmin[2], cmax[2])],
      alpha: rnd(nz(sys.alphaMin, 1), nz(sys.alphaMax, 1)),
      rot: rnd(nz(sys.rotMin, 0), nz(sys.rotMax, 0)),
      // 序列帧与闪烁都给一个随机起点：鸟群才不会齐刷刷新翅膀，星星才不会同明同灭。
      phase: Math.random(),
      osc: 0.5 + Math.random(),
    };
  }
  function updateParticles2d(sys, st, dt) {
    st.acc += nz(sys.rate, 0) * dt;
    const maxCount = Math.max(1, Math.floor(nz(sys.maxCount, 128)));
    while (st.acc >= 1) {
      st.acc -= 1;
      if (st.list.length < maxCount) st.list.push(spawnParticle2d(sys));
    }
    st.acc = Math.min(st.acc, 4);
    const grav = sys.gravity || [0, 0];
    const drag = nz(sys.drag, 0);
    const damp = drag > 0 ? 1 / (1 + drag * dt) : 1;
    const attrs = sys.attractions || [];
    for (let i = st.list.length - 1; i >= 0; i--) {
      const p = st.list[i];
      p.life += dt;
      if (p.life >= p.maxLife) { st.list.splice(i, 1); continue; }
      for (const a of attrs) {
        const dx = nz(a.x, 0) - p.x;
        const dy = nz(a.y, 0) - p.y;
        const th = nz(a.threshold, 1024);
        const d2 = dx * dx + dy * dy;
        if (d2 > th * th || d2 < 1) continue;
        const d = Math.sqrt(d2);
        const pull = nz(a.scale, 128) * (1 - d / th);
        p.vx += (dx / d) * pull * dt;
        p.vy += (dy / d) * pull * dt;
      }
      p.vx = (p.vx + nz(grav[0], 0) * dt) * damp;
      p.vy = (p.vy + nz(grav[1], 0) * dt) * damp;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
    }
  }

  // Shared unit quad geometry (-0.5 to 0.5)
  const quadBuf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
    -0.5, -0.5, 0.0, 1.0,
     0.5, -0.5, 1.0, 1.0,
    -0.5,  0.5, 0.0, 0.0,
     0.5,  0.5, 1.0, 0.0,
  ]), gl.STATIC_DRAW);

  function loadTexture(url, repeat, video) {
    const key = (repeat ? url + '|repeat' : url) + (video ? '|video' : '');
    if (textureCache.has(key)) return textureCache.get(key);
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([0,0,0,0]));
    const record = { texture: tex, loaded: false, width: 1, height: 1 };
    textureCache.set(key, record);

    if (video) {
      // 视频纹理：WE 把 MP4 塞在 .tex 里，清单侧已切成裸 MP4，这里逐帧把
      // <video> 重传成纹理。NPOT（3840×2160 这类）必须先设 CLAMP_TO_EDGE：
      // loadTexture 默认停在 REPEAT，NPOT + REPEAT 是 incomplete 纹理 ⇒ 采样恒黑。
      const el = document.createElement('video');
      el.muted = true;
      el.loop = true;
      el.playsInline = true;
      el.setAttribute('playsinline', '');
      el.preload = 'auto';
      el.style.position = 'absolute';
      el.style.left = '-1px';
      el.style.top = '-1px';
      el.style.width = '1px';
      el.style.height = '1px';
      el.style.opacity = '0';
      el.style.pointerEvents = 'none';
      document.body.appendChild(el);
      record.video = el;

      const upload = () => {
        if (record.disposed || el.readyState < 2 || !el.videoWidth || !el.videoHeight) return;
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, el);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        record.width = el.videoWidth;
        record.height = el.videoHeight;
        record.loaded = true;
        record.videoTime = el.currentTime;
      };
      record.uploadVideo = upload;
      el.addEventListener('loadeddata', upload);
      el.addEventListener('seeked', upload);
      // rVFC 只是「早一点拿到这一帧」的加速通道，不能当唯一通道（见 pumpVideoTextures）。
      if (typeof el.requestVideoFrameCallback === 'function') {
        const pump = () => {
          if (record.disposed) return;
          upload();
          el.requestVideoFrameCallback(pump);
        };
        el.requestVideoFrameCallback(pump);
      }
      videoTextures.add(record);
      el.src = url;
      if (isPaused) {
        el.pause();
      } else {
        const started = el.play();
        if (started && typeof started.catch === 'function') started.catch(function () {});
      }
      return record;
    }

    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      if (record.disposed) return;
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      const wrap = repeat ? gl.REPEAT : gl.CLAMP_TO_EDGE;
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, wrap);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, wrap);
      record.loaded = true;
      record.width = img.width;
      record.height = img.height;
    };
    img.src = url;
    return record;
  }

  // WE 内置的 \`util/white\` 不在包里（也不在磁盘上），相位槽拿不到图时用这张 1×1 白图：
  // 相位恒为 1 ⇒ 全场同相，正是这张包在 WE 里的实际表现。
  let whiteTexture = null;
  function whiteTex() {
    if (!whiteTexture) {
      whiteTexture = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, whiteTexture);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE,
        new Uint8Array([255, 255, 255, 255]));
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    }
    return whiteTexture;
  }

  // ---- 文字图层 --------------------------------------------------------------
  // WE 的文字不是贴图：CText 用 FreeType 把字形光栅化成位图，再按对象变换贴一张以原点
  // 为中心的 quad。这里等价地用 2D canvas + FontFace 生成位图，上传后走同一个 progBasic，
  // 于是视差、旋转、混合与图像图层共用一条路径。字形按白色光栅化、颜色交给 u_tint：
  // 同一句话在白天/夜晚两套颜色下复用同一张位图，换色不必重新光栅化。
  const TEXT_SUPERSAMPLE = 2;
  const TEXT_CACHE_LIMIT = 48;
  const textBitmapCache = new Map();
  const fontFaceCache = new Map();

  function clearTextBitmaps() {
    for (const rec of textBitmapCache.values()) gl.deleteTexture(rec.texture);
    textBitmapCache.clear();
  }

  /** 字体是异步的：未就位期间先用系统字栈出字，就位后整张位图缓存作废重建（不留旧字形）。 */
  function fontFaceOf(url) {
    if (!url) return { family: 'sans-serif', state: 'fallback' };
    const hit = fontFaceCache.get(url);
    if (hit) return hit;
    const rec = { family: 'we-scene-font-' + (fontFaceCache.size + 1), state: 'fallback' };
    fontFaceCache.set(url, rec);
    let face = null;
    try {
      face = new FontFace(rec.family, 'url(' + url + ')');
    } catch (e) {
      face = null;
    }
    if (!face) return rec;
    rec.state = 'loading';
    try {
      document.fonts.add(face);
      face.load().then(function () { rec.state = 'ready'; clearTextBitmaps(); })
        .catch(function () { rec.state = 'fallback'; clearTextBitmaps(); });
    } catch (e) {
      rec.state = 'fallback';
    }
    return rec;
  }

  const pad2 = (n) => (n < 10 ? '0' : '') + n;

  /**
   * 文字图层当前要显示的那句话。
   *
   * 时钟/日期不执行 WE 的脚本，而是按清单里抠出的档位与表本地拼接。日期脚本的写法因
   * 作者而异（竖排月名按字符换行、组间空行、日号补零表、年份逐字拆开、只显示星期），
   * 所以清单给的是带占位符的 format 模板，这里只做查表替换；暂停时播放器不推进
   * wallClock，画面里的时间就停在暂停那一刻——与 WE 停掉脚本更新的表现一致。
   */
  function textLineOf(layer, wallMs) {
    if (!layer.time) return layer.text == null ? '' : String(layer.text);
    const d = new Date(wallMs);
    if (layer.time === 'clock') {
      const sep = layer.delimiter == null ? ':' : String(layer.delimiter);
      let h = d.getHours();
      if (layer.hour24 === false) {
        h = h % 12;
        if (h === 0) h = 12;
      }
      let out = pad2(h) + sep + pad2(d.getMinutes());
      if (layer.seconds) out += sep + pad2(d.getSeconds());
      return out;
    }
    if (layer.time === 'date') {
      const months = layer.months || [];
      const days = layer.days || [];
      const dayTable = layer.dayTable || [];
      const month = months[d.getMonth()] != null ? String(months[d.getMonth()]) : String(d.getMonth() + 1);
      const year = String(d.getFullYear());
      const values = {
        month: month,
        weekday: days[d.getDay()] != null ? String(days[d.getDay()]) : '',
        day: String(d.getDate()),
        // 脚本里 dtt[date.getDate()] 是作者的补零/竖排日号表；没有表就用裸日号。
        dayTable: dayTable[d.getDate()] != null ? String(dayTable[d.getDate()]) : String(d.getDate()),
        year: layer.yearSplit ? year.split('').join(String(layer.yearSplit)) : year,
      };
      return String(layer.format || '').replace(/\{(month|weekday|dayTable|day|year)\}/g, function (all, key) {
        return values[key] != null ? values[key] : all;
      });
    }
    if (layer.time === 'greeting') {
      const labels = Array.isArray(layer.labels) ? layer.labels : [];
      const thresholds = Array.isArray(layer.thresholds) ? layer.thresholds : [];
      let index = 0;
      const hour = d.getHours();
      while (index < thresholds.length && hour >= Number(thresholds[index])) index += 1;
      return labels[index] == null ? '' : String(labels[index]);
    }
    // 清单里出现没见过的 time 档位：宁可不画，也不猜一个格式糊上去。
    return '';
  }

  // 帧率层的状态：WE 的 FPS 脚本按相邻帧间隔累计平均，每 refreshInterval 毫秒改写一次。
  // 这里也只数真正画出来的帧；暂停时不推进，恢复时清空重算，免得把暂停时长算成一帧。
  const fpsState = new Map();
  function fpsTextOf(layer, nowMs) {
    const label = layer.label == null ? 'fps:' : String(layer.label);
    const interval = typeof layer.refreshMs === 'number' && layer.refreshMs >= 0 ? layer.refreshMs : 500;
    const initial = layer.text == null ? label : String(layer.text);
    let state = fpsState.get(layer);
    if (!state) {
      state = { last: 0, start: 0, sum: 0, count: 0, text: initial };
      fpsState.set(layer, state);
    }
    if (isPaused) return state.text;
    if (!state.last) {
      state.last = nowMs;
      state.start = nowMs;
      return state.text;
    }
    const delta = nowMs - state.last;
    if (delta <= 0) return state.text;
    state.last = nowMs;
    state.sum += 1000 / delta;
    state.count += 1;
    if (nowMs - state.start >= interval) {
      state.text = label + ' ' + Math.round(state.sum / state.count);
      state.start = nowMs;
      state.sum = 0;
      state.count = 0;
    }
    return state.text;
  }

  function textBitmapOf(family, emPx, line) {
    const key = family + '|' + emPx + '|' + line;
    const hit = textBitmapCache.get(key);
    if (hit) return hit;
    const lines = line.split('\\n');
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    const font = emPx + 'px ' + family;
    ctx.font = font;
    // 行距吃字体自身的 ascent+descent，而不是固定 1.25em：真机 3723257973 的 WE
    // 参考图里 Quicksand 日期列行距 ≈1.16em、spincycle 的竖排 SUN ≈1.1em，固定
    // 1.25 会把三行 SUN 撑高约 30%，压到下方的时钟上。字体未就位时量到的是系统
    // 字栈，fontFaceOf 加载完成后会清掉位图缓存按真字体重量。
    const metrics = ctx.measureText('Mg');
    const metricPx = (metrics.fontBoundingBoxAscent || 0) + (metrics.fontBoundingBoxDescent || 0);
    const lineHeight = Math.max(1, Math.round(metricPx > 0 ? metricPx : emPx * 1.25));
    let widest = 1;
    for (const one of lines) widest = Math.max(widest, ctx.measureText(one).width);
    canvas.width = Math.max(1, Math.ceil(widest) + 2);
    canvas.height = Math.max(1, lineHeight * lines.length + 2);
    // 改画布尺寸会重置上下文状态，字体与对齐必须重设。
    ctx.font = font;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#ffffff';
    for (let i = 0; i < lines.length; i++) {
      ctx.fillText(lines[i], canvas.width / 2, lineHeight * (i + 0.5) + 1);
    }
    const texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, canvas);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    const rec = { texture: texture, width: canvas.width, height: canvas.height };
    textBitmapCache.set(key, rec);
    // 带秒的时钟每 tick 换一句话：按插入序淘汰，纹理数与位图数都有上界。
    if (textBitmapCache.size > TEXT_CACHE_LIMIT) {
      const oldest = textBitmapCache.keys().next().value;
      gl.deleteTexture(textBitmapCache.get(oldest).texture);
      textBitmapCache.delete(oldest);
    }
    return rec;
  }

  function drawTextLayer(layer, aPos, aUv, sceneW, sceneH, wallMs, nowMs, proj) {
    const line = layer.time === 'fps' ? fpsTextOf(layer, nowMs) : textLineOf(layer, wallMs);
    if (!line) return;
    const face = fontFaceOf(layer.font);
    const emPx = Math.max(1, Math.round((layer.fontSize || 0) * TEXT_SUPERSAMPLE));
    const rec = textBitmapOf(face.family, emPx, line);
    const frame = layerFrame(layer, sceneW, sceneH);
    const model = mat4Transform2D(frame[0], frame[1],
      rec.width / TEXT_SUPERSAMPLE, rec.height / TEXT_SUPERSAMPLE, frame[2]);
    gl.useProgram(progBasic);
    gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 16, 0);
    gl.vertexAttribPointer(aUv, 2, gl.FLOAT, false, 16, 8);
    gl.uniformMatrix4fv(gl.getUniformLocation(progBasic, 'u_proj'), false, proj);
    gl.uniformMatrix4fv(gl.getUniformLocation(progBasic, 'u_model'), false, model);
    gl.uniform4f(gl.getUniformLocation(progBasic, 'u_uvRect'), 0, 0, 1, 1);
    gl.uniform1f(gl.getUniformLocation(progBasic, 'u_alpha'), layer.alpha != null ? layer.alpha : 1);
    const tint = layer.color || [1, 1, 1];
    gl.uniform3f(gl.getUniformLocation(progBasic, 'u_tint'), tint[0], tint[1], tint[2]);
    gl.uniform1f(gl.getUniformLocation(progBasic, 'u_bright'), 1);
    gl.uniform1f(gl.getUniformLocation(progBasic, 'u_power'), 1);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, rec.texture);
    gl.uniform1i(gl.getUniformLocation(progBasic, 'u_tex'), 0);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  // FBO setup for reflection passes
  let fbo = null, fboTex = null, fboWidth = 0, fboHeight = 0;
  function ensureFbo(w, h) {
    if (fbo && fboWidth === w && fboHeight === h) return;
    fboWidth = w; fboHeight = h;
    if (fbo) gl.deleteFramebuffer(fbo);
    if (fboTex) gl.deleteTexture(fboTex);
    fbo = gl.createFramebuffer();
    fboTex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, fboTex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, fboTex, 0);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  }

  function mat4Ortho(left, right, bottom, top, near, far) {
    const lr = 1 / (left - right);
    const bt = 1 / (bottom - top);
    const nf = 1 / (near - far);
    return new Float32Array([
      -2 * lr, 0, 0, 0,
      0, -2 * bt, 0, 0,
      0, 0, 2 * nf, 0,
      (left + right) * lr, (top + bottom) * bt, (far + near) * nf, 1
    ]);
  }

  function mat4Perspective(fovRad, aspect, near, far) {
    const f = 1.0 / Math.tan(fovRad / 2);
    const nf = 1.0 / (near - far);
    return new Float32Array([
      f / aspect, 0, 0, 0,
      0, f, 0, 0,
      0, 0, (far + near) * nf, -1,
      0, 0, 2 * far * near * nf, 0
    ]);
  }

  function mat4LookAt(eye, center, up) {
    let zx = eye[0] - center[0], zy = eye[1] - center[1], zz = eye[2] - center[2];
    let len = Math.hypot(zx, zy, zz) || 1;
    zx /= len; zy /= len; zz /= len;

    let xx = up[1] * zz - up[2] * zy, xy = up[2] * zx - up[0] * zz, xz = up[0] * zy - up[1] * zx;
    len = Math.hypot(xx, xy, xz) || 1;
    xx /= len; xy /= len; xz /= len;

    let yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;

    return new Float32Array([
      xx, yx, zx, 0,
      xy, yy, zy, 0,
      xz, yz, zz, 0,
      -(xx * eye[0] + xy * eye[1] + xz * eye[2]),
      -(yx * eye[0] + yy * eye[1] + yz * eye[2]),
      -(zx * eye[0] + zy * eye[1] + zz * eye[2]),
      1
    ]);
  }

  function mat4Transform3D(origin, angles, scale) {
    const ox = origin[0] || 0, oy = origin[1] || 0, oz = origin[2] || 0;
    const sx = scale[0] || 1, sy = scale[1] || 1, sz = scale[2] || 1;
    const ax = (angles[0] || 0) * Math.PI / 180;
    const ay = (angles[1] || 0) * Math.PI / 180;
    const az = (angles[2] || 0) * Math.PI / 180;

    const cx = Math.cos(ax), sxn = Math.sin(ax);
    const cy = Math.cos(ay), syn = Math.sin(ay);
    const cz = Math.cos(az), szn = Math.sin(az);

    const m00 = (cy * cz) * sx;
    const m01 = (cx * szn + sxn * syn * cz) * sx;
    const m02 = (sxn * szn - cx * syn * cz) * sx;

    const m10 = (-cy * szn) * sy;
    const m11 = (cx * cz - sxn * syn * szn) * sy;
    const m12 = (sxn * cz + cx * syn * szn) * sy;

    const m20 = syn * sz;
    const m21 = (-sxn * cy) * sz;
    const m22 = (cx * cy) * sz;

    return new Float32Array([
      m00, m01, m02, 0,
      m10, m11, m12, 0,
      m20, m21, m22, 0,
      ox,  oy,  oz,  1
    ]);
  }

  function mat3NormalMatrix(m4) {
    return new Float32Array([
      m4[0], m4[1], m4[2],
      m4[4], m4[5], m4[6],
      m4[8], m4[9], m4[10]
    ]);
  }

  const modelGpuCache = new Map();
  // Preset hot-swaps rebuild the manifest objects but keep the mesh topology, so
  // geometry buffers are keyed by the stable meshKey instead of object identity.
  function meshCacheKey(mesh) {
    return (mesh && typeof mesh.meshKey === 'string' && mesh.meshKey) ? mesh.meshKey : mesh;
  }
  function getGpuMesh(mesh) {
    const cacheKey = meshCacheKey(mesh);
    if (modelGpuCache.has(cacheKey)) return modelGpuCache.get(cacheKey);

    function b64ToF32(b64) {
      const bin = atob(b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return new Float32Array(bytes.buffer);
    }
    function b64ToU16(b64) {
      const bin = atob(b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return new Uint16Array(bytes.buffer);
    }
    function b64ToU32(b64) {
      const bin = atob(b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return new Uint32Array(bytes.buffer);
    }
    // Meshes above 65535 vertices carry u32 indices (mesh.idx32), which
    // WebGL1 only exposes via OES_element_index_uint.
    const uintIndexExt = gl.getExtension('OES_element_index_uint');

    const basePos = b64ToF32(mesh.posB64);
    const skinned = Boolean(mesh.skinIndicesB64 && mesh.skinWeightsB64);
    const posBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, posBuf);
    gl.bufferData(gl.ARRAY_BUFFER, basePos, skinned ? gl.DYNAMIC_DRAW : gl.STATIC_DRAW);

    const normBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, normBuf);
    gl.bufferData(gl.ARRAY_BUFFER, b64ToF32(mesh.normB64), gl.STATIC_DRAW);

    const uvBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, uvBuf);
    gl.bufferData(gl.ARRAY_BUFFER, b64ToF32(mesh.uvB64), gl.STATIC_DRAW);

    const idxBuf = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, idxBuf);
    const idx32 = Boolean(mesh.idx32) && uintIndexExt;
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx32 ? b64ToU32(mesh.indicesB64) : b64ToU16(mesh.indicesB64), gl.STATIC_DRAW);

    const gpu = {
      posBuf, normBuf, uvBuf, idxBuf,
      iCount: mesh.iCount,
      idxType: idx32 ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT,
      basePos,
      skinned,
      vCount: basePos.length / 3,
      skinIndices: skinned ? b64ToU16(mesh.skinIndicesB64) : null,
      skinWeights: skinned ? b64ToF32(mesh.skinWeightsB64) : null,
      skinnedPos: skinned ? new Float32Array(basePos.length) : null,
    };
    modelGpuCache.set(cacheKey, gpu);
    return gpu;
  }

  /* ---------------- preset hot-swap ---------------- */
  // A preset rebuild produces a new manifest for the same package: layer
  // placement/material values change, mesh topology does not. Keep GPU meshes
  // and live textures, drop everything the new manifest stopped referencing.
  function collectLiveMeshKeys(data, out) {
    for (const layer of data.layers || []) {
      if (layer && layer.mesh) out.add(meshCacheKey(layer.mesh));
    }
    for (const model of data.models || []) {
      for (const mesh of model.meshes || []) {
        if (mesh) out.add(meshCacheKey(mesh));
      }
    }
    return out;
  }
  function pruneGpuMeshes(liveKeys) {
    for (const [key, gpu] of modelGpuCache) {
      if (liveKeys.has(key)) continue;
      gl.deleteBuffer(gpu.posBuf);
      gl.deleteBuffer(gpu.normBuf);
      gl.deleteBuffer(gpu.uvBuf);
      gl.deleteBuffer(gpu.idxBuf);
      modelGpuCache.delete(key);
    }
  }
  function collectLiveTextureUrls(node, out, depth) {
    if (node == null || depth > 16) return;
    if (typeof node === 'string') {
      if (node.lastIndexOf('blob:', 0) === 0) out.add(node);
      return;
    }
    if (Array.isArray(node)) {
      for (const item of node) collectLiveTextureUrls(item, out, depth + 1);
      return;
    }
    if (typeof node === 'object') {
      for (const key in node) {
        if (Object.prototype.hasOwnProperty.call(node, key)) {
          collectLiveTextureUrls(node[key], out, depth + 1);
        }
      }
    }
  }
  function pruneTextures(liveUrls) {
    for (const [key, record] of textureCache) {
      let url = key;
      if (url.endsWith('|video')) url = url.slice(0, -6);
      if (url.endsWith('|repeat')) url = url.slice(0, -7);
      if (liveUrls.has(url)) continue;
      // An in-flight Image may still fire onload; its record must not touch the
      // deleted texture object afterwards.
      record.disposed = true;
      if (record.video) {
        // 换预设后不再被引用的视频必须停播并卸掉：留着会持续解码 4K 帧。
        try {
          record.video.pause();
          record.video.removeAttribute('src');
          record.video.load();
        } catch (e) {}
        if (record.video.parentNode) record.video.parentNode.removeChild(record.video);
        videoTextures.delete(record);
      }
      gl.deleteTexture(record.texture);
      textureCache.delete(key);
    }
  }

  /**
   * 每帧把视频纹理重新采上来。
   *
   * 为什么不能只靠 requestVideoFrameCallback：rVFC 只在「解码帧真的被合成器呈现」时
   * 回调，而这里的 <video> 是 1px / opacity:0 / 移出视口的纯纹理源，Chromium 不呈现它
   * ——真机（WebView2/Edge 155）实测 currentTime 按 1:1 推进、totalVideoFrames 每秒 +30、
   * droppedVideoFrames 同步上涨，而 rVFC 连续 12 秒一次都不来。只挂 rVFC 时美术层永久
   * 停在最后一次 loadeddata 的那张图上，用户看到的就是「艺术字还在走，壁纸不动了」。
   * 播放被拒（自动播放策略）也在这里自愈：创建时那次 play() 的 rejection 是吞掉的，
   * 不重试就再没有第二次机会。
   */
  function pumpVideoTextures(now) {
    if (videoTextures.size === 0) return;
    const mayRetry = now - lastVideoPlayRetry > 1000;
    for (const record of videoTextures) {
      if (record.disposed || !record.video) {
        videoTextures.delete(record);
        continue;
      }
      const el = record.video;
      if (!isPaused && el.paused && el.readyState >= 2) {
        if (mayRetry) {
          lastVideoPlayRetry = now;
          const started = el.play();
          if (started && typeof started.catch === 'function') started.catch(function () {});
        }
        continue;
      }
      if (el.readyState < 2 || el.currentTime === record.videoTime) continue;
      record.uploadVideo();
    }
  }
  function swapSceneManifest(next) {
    if (!next || typeof next !== 'object') return;
    sceneData = next;
    pruneGpuMeshes(collectLiveMeshKeys(next, new Set()));
    const liveUrls = new Set();
    collectLiveTextureUrls(next, liveUrls, 0);
    pruneTextures(liveUrls);
    activeParticles = activeParticles.filter((p) => !p.system.texUrl || liveUrls.has(p.system.texUrl));
    // Bone/clip arrays are decoded from this manifest's puppet payloads.
    puppetRuntimeCache.clear();
    poseCache.clear();
    fpsState.clear();
  }

  function mat4Transform2D(x, y, w, h, angleRad) {
    const c = Math.cos(angleRad || 0);
    const s = Math.sin(angleRad || 0);
    return new Float32Array([
      w * c,  w * s,  0, 0,
     -h * s,  h * c,  0, 0,
      0,      0,      1, 0,
      x,      y,      0, 1
    ]);
  }

  /* ---------------- puppet skinning + MDLA clips ---------------- */
  // Manifest payloads carry pose data only; the player still owns the clock.
  // Clips are sampled in frames: base clips write the pose directly, additive
  // clips (blink / breath) add key(t) - key(0) on top and loop independently.
  function decodeB64(b64) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
  }
  function b64ToF32(b64) {
    return new Float32Array(decodeB64(b64).buffer);
  }
  function b64ToU16(b64) {
    return new Uint16Array(decodeB64(b64).buffer);
  }
  function mat4MultiplyOut(out, o, a, ao, b, bo) {
    for (let col = 0; col < 4; col++) {
      const b0 = b[bo + col * 4], b1 = b[bo + col * 4 + 1];
      const b2 = b[bo + col * 4 + 2], b3 = b[bo + col * 4 + 3];
      for (let row = 0; row < 4; row++) {
        out[o + col * 4 + row] =
          a[ao + row] * b0 + a[ao + 4 + row] * b1 +
          a[ao + 8 + row] * b2 + a[ao + 12 + row] * b3;
      }
    }
  }
  // Same Euler convention as mat4Transform3D (R = Rx * Ry * Rz), radians.
  function mat4ComposeTrs(out, o, trs, at) {
    const rx = trs[at + 3], ry = trs[at + 4], rz = trs[at + 5];
    const sx = trs[at + 6], sy = trs[at + 7], sz = trs[at + 8];
    const cx = Math.cos(rx), sxn = Math.sin(rx);
    const cy = Math.cos(ry), syn = Math.sin(ry);
    const cz = Math.cos(rz), szn = Math.sin(rz);
    out[o]      = (cy * cz) * sx;
    out[o + 1]  = (cx * szn + sxn * syn * cz) * sx;
    out[o + 2]  = (sxn * szn - cx * syn * cz) * sx;
    out[o + 4]  = (-cy * szn) * sy;
    out[o + 5]  = (cx * cz - sxn * syn * szn) * sy;
    out[o + 6]  = (sxn * cz + cx * syn * szn) * sy;
    out[o + 8]  = syn * sz;
    out[o + 9]  = (-sxn * cy) * sz;
    out[o + 10] = (cx * cy) * sz;
    out[o + 3] = out[o + 7] = out[o + 11] = 0;
    out[o + 12] = trs[at];
    out[o + 13] = trs[at + 1];
    out[o + 14] = trs[at + 2];
    out[o + 15] = 1;
  }
  function puppetRuntime(puppetId) {
    if (puppetRuntimeCache.has(puppetId)) return puppetRuntimeCache.get(puppetId);
    const payload = sceneData.puppets && sceneData.puppets[puppetId];
    let runtime = null;
    if (payload && payload.boneCount > 0) {
      runtime = {
        id: puppetId,
        payload,
        boneCount: payload.boneCount,
        parents: payload.parents || [],
        inverseBind: b64ToF32(payload.inverseBindB64),
        bindTrs: b64ToF32(payload.bindTrsB64),
        clips: new Map(),
        local: new Float32Array(payload.boneCount * 9),
        tmp: new Float32Array(16),
        state: new Uint8Array(payload.boneCount),
      };
    }
    puppetRuntimeCache.set(puppetId, runtime);
    return runtime;
  }
  function clipTracks(runtime, clip) {
    if (runtime.clips.has(clip.id)) return runtime.clips.get(clip.id);
    const tracks = clip.tracks.map((track) => ({ bone: track.bone, keys: b64ToF32(track.keysB64) }));
    runtime.clips.set(clip.id, tracks);
    return tracks;
  }
  function animationSignature(animation) {
    if (!animation || animation.length === 0) return '';
    return animation.map((a) =>
      a.id + ':' + (a.additive ? 1 : 0) + ':' + a.offset + ':' + a.rate + ':' + a.blend).join(',');
  }
  function buildPoseLocal(runtime, animation) {
    const local = runtime.local;
    local.set(runtime.bindTrs);
    if (!animation || animation.length === 0) return;
    const v0 = new Float32Array(9);
    const v1 = new Float32Array(9);
    for (const entry of animation) {
      const clip = runtime.payload.clips[entry.id];
      if (!clip) continue;
      const frames = Math.max(1, clip.frameCount);
      const loops = animTime * (entry.rate || 1) * (clip.fps || 30) / frames;
      const phase = (((entry.offset || 0) + loops) % 1 + 1) % 1;
      const at = phase * frames;
      const i0 = Math.min(frames - 1, Math.floor(at));
      const i1 = Math.min(frames, i0 + 1);
      const t = at - i0;
      const blend = entry.blend == null ? 1 : entry.blend;
      for (const track of clipTracks(runtime, clip)) {
        const keys = track.keys;
        const o = track.bone * 9;
        if (o < 0 || o + 9 > local.length) continue;
        for (let k = 0; k < 9; k++) {
          const a = keys[i0 * 9 + k];
          v1[k] = a + (keys[i1 * 9 + k] - a) * t;
        }
        if (!entry.additive) {
          for (let k = 0; k < 9; k++) local[o + k] = v1[k];
          continue;
        }
        for (let k = 0; k < 9; k++) v0[k] = keys[k];
        for (let k = 0; k < 6; k++) local[o + k] += (v1[k] - v0[k]) * blend;
        for (let k = 6; k < 9; k++) {
          const ratio = v0[k] ? v1[k] / v0[k] : 1;
          local[o + k] *= 1 + (ratio - 1) * blend;
        }
      }
    }
  }
  function puppetWorld(runtime, out) {
    const n = runtime.boneCount;
    const parents = runtime.parents;
    const state = runtime.state;
    state.fill(0);
    const build = (i, depth) => {
      if (i < 0 || i >= n || state[i] === 2 || state[i] === 1 || depth > 64) return;
      state[i] = 1;
      const p = parents[i];
      const hasParent = p != null && p >= 0 && p < n;
      if (hasParent) build(p, depth + 1);
      mat4ComposeTrs(out, i * 16, runtime.local, i * 9);
      if (hasParent && state[p] === 2) {
        runtime.tmp.set(out.subarray(i * 16, i * 16 + 16));
        mat4MultiplyOut(out, i * 16, out, p * 16, runtime.tmp, 0);
      }
      state[i] = 2;
    };
    for (let i = 0; i < n; i++) build(i, 0);
  }
  function puppetPose(puppetId, animation) {
    const runtime = puppetRuntime(puppetId);
    if (!runtime) return null;
    const key = puppetId + '|' + animationSignature(animation);
    let pose = poseCache.get(key);
    if (!pose) {
      pose = {
        runtime,
        world: new Float32Array(runtime.boneCount * 16),
        skin: new Float32Array(runtime.boneCount * 16),
      };
      poseCache.set(key, pose);
    }
    if (pose.frame === frameSeq) return pose;
    pose.frame = frameSeq;
    buildPoseLocal(runtime, animation);
    puppetWorld(runtime, pose.world);
    for (let i = 0; i < runtime.boneCount; i++) {
      mat4MultiplyOut(pose.skin, i * 16, pose.world, i * 16, runtime.inverseBind, i * 16);
    }
    return pose;
  }
  function skinGpuMesh(gpu, pose) {
    const base = gpu.basePos, out = gpu.skinnedPos;
    const indices = gpu.skinIndices, weights = gpu.skinWeights;
    const skin = pose.skin;
    for (let v = 0, o = 0; v < gpu.vCount; v++, o += 3) {
      const x = base[o], y = base[o + 1], z = base[o + 2];
      let rx = 0, ry = 0, rz = 0;
      for (let k = 0; k < 4; k++) {
        const w = weights[v * 4 + k];
        if (!w) continue;
        const b = indices[v * 4 + k] * 16;
        rx += w * (skin[b] * x + skin[b + 4] * y + skin[b + 8] * z + skin[b + 12]);
        ry += w * (skin[b + 1] * x + skin[b + 5] * y + skin[b + 9] * z + skin[b + 13]);
        rz += w * (skin[b + 2] * x + skin[b + 6] * y + skin[b + 10] * z + skin[b + 14]);
      }
      out[o] = rx;
      out[o + 1] = ry;
      out[o + 2] = rz;
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, gpu.posBuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, out);
  }
  // Rigid subtree delta of an attachment (hair follows the animated head
  // bone): rotate around the bind socket's screen point, then translate.
  function followDelta(follow) {
    if (!follow) return null;
    const pose = puppetPose(follow.puppetId, window.__weDebugAnim || follow.animation);
    if (!pose) return null;
    const b = follow.bone * 16;
    const w = pose.world;
    const l = follow.local;
    const a00 = w[b] * l[0] + w[b + 4] * l[1] + w[b + 8] * l[2];
    const a10 = w[b + 1] * l[0] + w[b + 5] * l[1] + w[b + 9] * l[2];
    const tx = w[b] * l[12] + w[b + 4] * l[13] + w[b + 8] * l[14] + w[b + 12];
    const ty = w[b + 1] * l[12] + w[b + 5] * l[13] + w[b + 9] * l[14] + w[b + 13];
    const angle = Math.atan2(a10, a00) - follow.bindAngle;
    const m = follow.screenMap;
    const bs = follow.bindSocket;
    const so = follow.screenOrigin;
    const dx = tx - bs[0];
    const dy = ty - bs[1];
    return {
      sx: so[0] + bs[0] * m[0] + bs[1] * m[2],
      sy: so[1] + bs[0] * m[1] + bs[1] * m[3],
      dx: dx * m[0] + dy * m[2],
      dy: dx * m[1] + dy * m[3],
      angle,
    };
  }
  function layerFrame(layer, sceneW, sceneH) {
    const origin = layerOrigin(layer, sceneW, sceneH);
    let x = origin[0], y = origin[1];
    let angle = layer.angle || 0;
    const delta = followDelta(layer.follow);
    if (delta) {
      const rx = x - delta.sx;
      const ry = y - delta.sy;
      const c = Math.cos(delta.angle), s = Math.sin(delta.angle);
      x = delta.sx + rx * c - ry * s + delta.dx;
      y = delta.sy + rx * s + ry * c + delta.dy;
      angle += delta.angle;
    }
    return [x, y, angle];
  }

  // Puppet layer: MDLV vertices are already in model units, so w/h are a
  // per-unit scale (not a quad width). UVs come from the mesh (atlas space).
  function drawBasicMeshLayer(layer, aPos, aUv, sceneW, sceneH, elapsed, proj) {
    const gpu = getGpuMesh(layer.mesh);
    if (gpu.skinned && layer.puppetId) {
      const pose = puppetPose(layer.puppetId, window.__weDebugAnim || layer.animation);
      if (pose) skinGpuMesh(gpu, pose);
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, gpu.posBuf);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 12, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, gpu.uvBuf);
    gl.vertexAttribPointer(aUv, 2, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gpu.idxBuf);

    const origin = layerFrame(layer, sceneW, sceneH);
    const meshScale = layer.meshScale || [1, 1];
    const model = mat4Transform2D(origin[0], origin[1], meshScale[0], meshScale[1], origin[2]);
    gl.uniformMatrix4fv(gl.getUniformLocation(progBasic, 'u_proj'), false, proj);
    gl.uniformMatrix4fv(gl.getUniformLocation(progBasic, 'u_model'), false, model);
    gl.uniform4f(gl.getUniformLocation(progBasic, 'u_uvRect'), 0, 0, 1, 1);
    gl.uniform1f(gl.getUniformLocation(progBasic, 'u_time'), elapsed);
    gl.uniform1f(gl.getUniformLocation(progBasic, 'u_alpha'), layer.alpha != null ? layer.alpha : 1.0);
    gl.uniform3f(gl.getUniformLocation(progBasic, 'u_tint'), 1, 1, 1);
    gl.uniform1f(gl.getUniformLocation(progBasic, 'u_sway'), 0);
    gl.uniform1f(gl.getUniformLocation(progBasic, 'u_sway_speed'), 1);

    const texRec = loadTexture(layer.texUrl);
    if (texRec.loaded) {
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, texRec.texture);
      gl.uniform1i(gl.getUniformLocation(progBasic, 'u_tex'), 0);
    }
    gl.drawElements(gl.TRIANGLES, gpu.iCount, gpu.idxType, 0);

    // Every quad draw after this one relies on the shared quad layout.
    gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 16, 0);
    gl.vertexAttribPointer(aUv, 2, gl.FLOAT, false, 16, 8);
  }

  function spawnParticle(emitter, system) {
    const lifeMin = system.lifeMin || 3;
    const lifeMax = system.lifeMax || 5;
    const lifetime = lifeMin + Math.random() * (lifeMax - lifeMin);

    // Position
    let x = 0, y = 0, vx = 0, vy = 0;
    if (system.type === 'meteor') {
      x = 500 + Math.random() * 3000;
      y = 1200 + Math.random() * 800;
      const speed = 700 + Math.random() * 500;
      vx = -speed * 0.85;
      vy = -speed * 0.52;
    } else { // fireflies / sparkles
      x = 200 + Math.random() * 3440;
      y = 100 + Math.random() * 900;
      vx = (Math.random() - 0.5) * 25;
      vy = 10 + Math.random() * 20;
    }

    const size = system.size || (15 + Math.random() * 20);
    activeParticles.push({
      system,
      x, y, vx, vy,
      size,
      life: 0,
      maxLife: lifetime,
      color: system.color || [1, 1, 0.8, 1],
      trail: []
    });
  }

  function updateParticles(dt) {
    for (let i = activeParticles.length - 1; i >= 0; i--) {
      const p = activeParticles[i];
      p.life += dt;
      if (p.life >= p.maxLife) {
        activeParticles.splice(i, 1);
        continue;
      }
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      if (p.system.type === 'meteor') {
        p.trail.push({ x: p.x, y: p.y, life: p.life });
        if (p.trail.length > 8) p.trail.shift();
      } else {
        // Floating wander
        p.vx += (Math.random() - 0.5) * 15 * dt;
        p.vy += (Math.random() - 0.5) * 15 * dt;
      }
    }
  }
  // FBO for screen-space reflection (grid floor)
  let reflFbo = null;
  let reflTex = null;
  let reflDepth = null;
  let reflW = 0, reflH = 0;
  function ensureReflFbo(w, h) {
    if (reflW === w && reflH === h && reflFbo) return;
    if (reflFbo) { gl.deleteFramebuffer(reflFbo); gl.deleteTexture(reflTex); gl.deleteRenderbuffer(reflDepth); }
    reflFbo = gl.createFramebuffer();
    reflTex = gl.createTexture();
    reflDepth = gl.createRenderbuffer();
    gl.bindTexture(gl.TEXTURE_2D, reflTex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.bindRenderbuffer(gl.RENDERBUFFER, reflDepth);
    gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT16, w, h);
    gl.bindFramebuffer(gl.FRAMEBUFFER, reflFbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, reflTex, 0);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, reflDepth);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    reflW = w; reflH = h;
  }

  function renderFrame(now) {
    if (!sceneData) {
      requestAnimationFrame(render);
      return;
    }

    pumpVideoTextures(now);

    // Reset stale vertex-attribute bindings from the previous frame so a draw
    // that switches shaders never hits "no buffer is bound to enabled attribute".
    const _maxAttribs = gl.getParameter(gl.MAX_VERTEX_ATTRIBS);
    for (let i = 0; i < _maxAttribs; i++) gl.disableVertexAttribArray(i);

    const dt = Math.min((now - lastTime) / 1000, 0.1);
    lastTime = now;
    const elapsed = (now - startTime) / 1000;
    frameSeq++;

    if (!isPaused) {
      // Puppet animations run on their own clock so pause freezes the pose.
      animTime += dt;
      wallClock = Date.now();
      if (typeof window.__weDebugTime === 'number') animTime = window.__weDebugTime;
      // Spawn particles periodically
      if (sceneData.hasMeteors && Math.random() < dt * 1.8) {
        spawnParticle({}, { type: 'meteor', lifeMin: 1.2, lifeMax: 2.2, size: 28, color: [1, 0.95, 0.85, 1], texUrl: sceneData.meteorTex });
      }
      if (sceneData.hasFireflies && activeParticles.filter(p => p.system.type === 'firefly').length < 35) {
        spawnParticle({}, { type: 'firefly', lifeMin: 4, lifeMax: 8, size: 14, color: [0.8, 1.0, 0.5, 0.85], texUrl: sceneData.sparkleTex });
      }
      updateParticles(dt);
    }

    const width = window.innerWidth;
    const height = window.innerHeight;
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }

    if (sceneData.is3D && sceneData.models && sceneData.models.length > 0) {
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, width, height);
      const bg = sceneData.clearColor || [0.1, 0.1, 0.15];
      gl.clearColor(bg[0] * 0.4, bg[1] * 0.4, bg[2] * 0.4, 1.0);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST);
      gl.disable(gl.CULL_FACE);

      const isCarScene = Boolean(sceneData.carBodyColor);
      const aspect = width / height;
      const cam = sceneData.camera || { eye: [2.18, 1.98, 4.63], center: [0, 0.45, 0], up: [0, 1, 0], fov: 45 };
      const proj3D = mat4Perspective((cam.fov || 45) * Math.PI / 180, aspect, 0.1, 1000.0);

      // Camera animation: use scene-specific paths if available, otherwise slow orbit
      const camPaths = sceneData.cameraPaths;
      let eye, center, upVec;
      if (camPaths && camPaths.length > 0) {
        const totalDur = camPaths.reduce((s, p) => s + p.d, 0);
        const cycleTime = elapsed % totalDur;
        let accum = 0, seg = camPaths[0], segT = 0;
        for (const p of camPaths) {
          if (cycleTime < accum + p.d) { seg = p; segT = (cycleTime - accum) / p.d; break; }
          accum += p.d;
        }
        segT = segT * segT * (3 - 2 * segT);
        const lerp3 = (a, b, t) => [a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t, a[2]+(b[2]-a[2])*t];
        eye = lerp3(seg.e0, seg.e1, segT);
        center = lerp3(seg.c0, seg.c1, segT);
        upVec = lerp3(seg.u0, seg.u1, segT);
      } else if (sceneData.cameraStatic) {
        // Fixed scene camera (no animation paths)
        eye = [cam.eye[0], cam.eye[1], cam.eye[2]];
        center = [cam.center[0], cam.center[1], cam.center[2]];
        upVec = [cam.up[0], cam.up[1], cam.up[2]];
      } else {
        const cx = cam.center[0], cy = cam.center[1], cz = cam.center[2];
        const dx = cam.eye[0] - cx, dy = cam.eye[1] - cy, dz = cam.eye[2] - cz;
        const radius = Math.hypot(dx, dy, dz) || 4.5;
        const baseAngle = Math.atan2(dx, dz);
        const pitchAngle = Math.atan2(dy, Math.hypot(dx, dz));
        const yaw = baseAngle + elapsed * 0.05;
        const pitch = pitchAngle;
        eye = [cx + Math.sin(yaw) * Math.cos(pitch) * radius, cy + Math.sin(pitch) * radius, cz + Math.cos(yaw) * Math.cos(pitch) * radius];
        center = [cx, cy, cz];
        upVec = [0, 1, 0];
      }
      // Mouse parallax (only when the scene enables camera parallax)
      const parallaxOn = cameraParallaxEnabled();
      const targetYaw = parallaxOn ? (mouseX - 0.5) * 0.6 : 0;
      const targetPitch = parallaxOn ? (mouseY - 0.5) * 0.3 : 0;
      curRotY += (targetYaw - curRotY) * 0.04;
      curRotX += (targetPitch - curRotX) * 0.04;
      eye[0] += curRotY * 0.5;
      eye[1] += curRotX * 0.3;

      const view3D = mat4LookAt(eye, center, upVec);
      // Planar reflection: the FBO pass renders from a camera mirrored below
      // the floor plane (y=0), so the grid can sample it 1:1 by screen UV.
      const view3DRefl = mat4LookAt(
        [eye[0], -eye[1], eye[2]],
        [center[0], -center[1], center[2]],
        [upVec[0], -upVec[1], upVec[2]]);
      const bodyCol = sceneData.carBodyColor || [1, 0, 0];

      // (Re-)bind prog3D with all scene uniforms. Must be re-invoked after any
      // pass that switches to another program (bgLayers, billboards).
      function bindProg3D(viewOverride) {
        gl.useProgram(prog3D);
        gl.uniformMatrix4fv(gl.getUniformLocation(prog3D, 'u_proj'), false, proj3D);
        gl.uniformMatrix4fv(gl.getUniformLocation(prog3D, 'u_view'), false, viewOverride || view3D);
        gl.uniform3f(gl.getUniformLocation(prog3D, 'u_cameraPos'), eye[0], eye[1], eye[2]);
        gl.uniform1f(gl.getUniformLocation(prog3D, 'u_time'), elapsed);
        // WE-standard scene shading for generic scenes; car scenes keep their
        // dedicated paint/grid pipeline.
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_sceneStd'), isCarScene ? 0 : 1);
        // Engine-glow boost positions: origins of jet models (ricepod.vert).
        const jetPos = [];
        for (const model of sceneData.models) {
          const mName = (model.name || '').toLowerCase();
          const jetLike = mName.includes('jet') || (model.meshes || []).some((mm) => (mm.shader || '').toLowerCase().includes('jet'));
          if (jetLike && jetPos.length < 4) jetPos.push(model.origin || [0, 0, 0]);
        }
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_jetCount'), jetPos.length);
        for (let ji = 0; ji < 4; ji++) {
          const jp = jetPos[ji] || [0, 0, 0];
          gl.uniform3f(gl.getUniformLocation(prog3D, 'u_jetPos[' + ji + ']'), jp[0], jp[1], jp[2]);
        }
        // Ricepod uses lightDir (-0.577, 0.577, 0.577), car uses (0.577, 0.577, 0.577)
        gl.uniform3f(gl.getUniformLocation(prog3D, 'u_lightDir'), isCarScene ? 0.577 : -0.577, 0.577, 0.577);
        const amb = sceneData.clearColor || [0.1, 0.1, 0.15];
        // For car scenes use schemecolor as ambient; for others use brighter neutral
        const ambColor = isCarScene ? amb : [Math.max(amb[0], 0.3), Math.max(amb[1], 0.3), Math.max(amb[2], 0.35)];
        gl.uniform3f(gl.getUniformLocation(prog3D, 'u_ambientColor'), ambColor[0], ambColor[1], ambColor[2]);
        gl.uniform3f(gl.getUniformLocation(prog3D, 'u_paintColor'), bodyCol[0], bodyCol[1], bodyCol[2]);
      }
      bindProg3D();

      const locPos = gl.getAttribLocation(prog3D, 'a_pos');
      const locNorm = gl.getAttribLocation(prog3D, 'a_norm');
      const locUv = gl.getAttribLocation(prog3D, 'a_uv');
      gl.enableVertexAttribArray(locPos);
      gl.enableVertexAttribArray(locNorm);
      gl.enableVertexAttribArray(locUv);

      // Per-submesh specular params (from WE material JSONs)
      const specMap = {
        body: [0.4, 6], glass: [5, 50], interior: [0.2, 15],
        matte: [0.5, 10], taillights: [0.25, 10], wheel: [1, 10],
      };
      function getSpecParams(texUrl) {
        if (!texUrl) return [0.3, 10];
        for (const [k, v] of Object.entries(specMap)) {
          if (texUrl.includes(k)) return v;
        }
        return [0.3, 10];
      }

      // Render in correct order: skybox/dome, opaque, shadow, grid, glass/additive
      const skyboxModels = [];
      const domeModels = [];
      const opaqueModels = [];
      const shadowModels = [];
      const gridModels = [];
      const glassQueue = [];
      const additiveQueue = [];
      const translucentQueue = [];
      const neonGridQueue = [];

      for (const model of sceneData.models) {
        const mName = (model.name || '').toLowerCase();
        if (mName === 'skybox') { skyboxModels.push(model); continue; }
        if (mName === 'dome') { domeModels.push(model); continue; }
        if (mName === 'shadow') { shadowModels.push(model); continue; }
        if (mName === 'grid') { gridModels.push(model); continue; }
        // Material blending flags decide the queue per mesh; the model name
        // 'jet' heuristic stays as a fallback for legacy manifests.
        const opaqueMeshes = [];
        for (const mesh of model.meshes) {
          const shName = (mesh.shader || '').toLowerCase();
          if (shName === 'neongrid') neonGridQueue.push({ model, mesh });
          else if (mesh.additive || mName.includes('jet')) additiveQueue.push({ model, mesh });
          else if (mesh.translucent) translucentQueue.push({ model, mesh });
          else opaqueMeshes.push(mesh);
        }
        if (opaqueMeshes.length > 0) opaqueModels.push({ ...model, meshes: opaqueMeshes });
      }

      // Helper to draw a mesh with given uniforms
      function drawMesh(model, mesh, flags) {
        let modelMat = mat4Transform3D(model.origin, model.angles, model.scale);
        // Skybox/aurora/thunder follow the camera position (WE shaders add
        // g_EyePosition to the vertex instead of a model transform).
        if (flags.skybox || flags.followEye) {
          modelMat = mat4Transform3D([eye[0], eye[1], eye[2]], model.angles, model.scale);
        }
        gl.uniformMatrix4fv(gl.getUniformLocation(prog3D, 'u_model'), false, modelMat);
        const normMat = mat3NormalMatrix(modelMat);
        gl.uniformMatrix3fv(gl.getUniformLocation(prog3D, 'u_normMat'), false, normMat);

        const gpu = getGpuMesh(mesh);
        gl.bindBuffer(gl.ARRAY_BUFFER, gpu.posBuf);
        gl.vertexAttribPointer(locPos, 3, gl.FLOAT, false, 0, 0);
        gl.bindBuffer(gl.ARRAY_BUFFER, gpu.normBuf);
        gl.vertexAttribPointer(locNorm, 3, gl.FLOAT, false, 0, 0);
        gl.bindBuffer(gl.ARRAY_BUFFER, gpu.uvBuf);
        gl.vertexAttribPointer(locUv, 2, gl.FLOAT, false, 0, 0);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gpu.idxBuf);

        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_isDome'), flags.dome ? 1 : 0);
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_isShadow'), flags.shadow ? 1 : 0);
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_isGrid'), flags.grid ? 1 : 0);
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_isSkybox'), flags.skybox ? 1 : 0);
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_isSelfIllum'), flags.selfIllum ? 1 : 0);
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_isCarBody'), flags.body ? 1 : 0);
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_isGlass'), flags.glass ? 1 : 0);
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_isJet'), flags.jet ? 1 : 0);
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_isAurora'), flags.aurora ? 1 : 0);
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_isThunder'), flags.thunder ? 1 : 0);
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_isBg'), flags.bg ? 1 : 0);
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_isNeonSun'), flags.neonSun ? 1 : 0);
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_gradFade'), mesh.gradFade ? 1 : 0);
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_hasTint'), mesh.tint || flags.neonSun ? 1 : 0);
        const uc = mesh.userColors || {};
        let tintCol = mesh.tint || [1, 1, 1];
        let tint2Col = mesh.tint2 || tintCol;
        if (flags.neonSun) {
          tintCol = uc.colorsuntop || tintCol;
          tint2Col = uc.colorsunbottom || tint2Col;
        }
        gl.uniform3f(gl.getUniformLocation(prog3D, 'u_tint'), tintCol[0], tintCol[1], tintCol[2]);
        gl.uniform3f(gl.getUniformLocation(prog3D, 'u_tint2'), tint2Col[0], tint2Col[1], tint2Col[2]);
        // Second pass texture (bg pattern overlay), repeat-wrapped like the bg clouds.
        if (mesh.texUrl2) {
          const tex2Rec = loadTexture(mesh.texUrl2, true);
          if (tex2Rec.loaded) {
            gl.activeTexture(gl.TEXTURE1);
            gl.bindTexture(gl.TEXTURE_2D, tex2Rec.texture);
            gl.uniform1i(gl.getUniformLocation(prog3D, 'u_tex2'), 1);
            gl.activeTexture(gl.TEXTURE0);
          }
        }

        // WE material depth flags (orbital glows disable both).
        if (mesh.noDepthTest) gl.disable(gl.DEPTH_TEST);
        if (mesh.noDepthWrite) gl.depthMask(false);

        const sp = getSpecParams(mesh.texUrl);
        gl.uniform1f(gl.getUniformLocation(prog3D, 'u_specStrength'), sp[0]);
        gl.uniform1f(gl.getUniformLocation(prog3D, 'u_specPower'), sp[1]);

        if (flags.body) {
          const strCol = sceneData.carStripesColor || [0, 0, 0];
          gl.uniform3f(gl.getUniformLocation(prog3D, 'u_paintColor'), bodyCol[0], bodyCol[1], bodyCol[2]);
          gl.uniform3f(gl.getUniformLocation(prog3D, 'u_stripeColor'), strCol[0], strCol[1], strCol[2]);
        }

        // Load texture for all meshes that have one (including skybox)
        if (mesh.texUrl && !flags.dome && !flags.shadow && !flags.grid) {
          const texRec = loadTexture(mesh.texUrl, flags.aurora || flags.bg);
          if (texRec.loaded) {
            gl.activeTexture(gl.TEXTURE0);
            gl.bindTexture(gl.TEXTURE_2D, texRec.texture);
            gl.uniform1i(gl.getUniformLocation(prog3D, 'u_tex'), 0);
            gl.uniform1i(gl.getUniformLocation(prog3D, 'u_hasTex'), 1);
          } else {
            gl.uniform1i(gl.getUniformLocation(prog3D, 'u_hasTex'), 0);
            gl.uniform3f(gl.getUniformLocation(prog3D, 'u_color'), 0.7, 0.7, 0.75);
          }
        } else {
          gl.uniform1i(gl.getUniformLocation(prog3D, 'u_hasTex'), 0);
          gl.uniform3f(gl.getUniformLocation(prog3D, 'u_color'), 0.65, 0.68, 0.72);
        }

        gl.drawElements(gl.TRIANGLES, gpu.iCount, gpu.idxType, 0);

        if (mesh.noDepthTest) gl.enable(gl.DEPTH_TEST);
        if (mesh.noDepthWrite) gl.depthMask(true);
      }

      // --- Pass 1: Render to FBO for reflection source (if car scene with grid) ---
      const hasGrid = isCarScene && gridModels.length > 0;
      if (hasGrid) {
        bindProg3D(view3DRefl); // mirrored camera for the reflection pass
        ensureReflFbo(width, height);
        // Unbind the reflection texture before rendering into its own FBO
        // (avoids a framebuffer/texture feedback loop from the last frame).
        gl.activeTexture(gl.TEXTURE1);
        gl.bindTexture(gl.TEXTURE_2D, null);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindFramebuffer(gl.FRAMEBUFFER, reflFbo);
        gl.viewport(0, 0, width, height);
        gl.clearColor(bg[0] * 0.4, bg[1] * 0.4, bg[2] * 0.4, 1.0);
        gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

        // Dome to FBO
        gl.depthMask(false);
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_hasReflTex'), 0);
        for (const model of domeModels) {
          for (const mesh of model.meshes) drawMesh(model, mesh, { dome: true });
        }
        gl.depthMask(true);

        // Opaque car to FBO
        gl.disable(gl.BLEND);
        for (const model of opaqueModels) {
          for (const mesh of model.meshes) {
            const isBody = Boolean(isCarScene && mesh.texUrl && mesh.texUrl.includes('body'));
            const isGlass = Boolean(mesh.texUrl && mesh.texUrl.includes('glass'));
            if (!isGlass) drawMesh(model, mesh, { body: isBody });
          }
        }
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      }

      // --- Pass 2: Render to screen ---
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, width, height);
      gl.clearColor(bg[0] * 0.4, bg[1] * 0.4, bg[2] * 0.4, 1.0);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      bindProg3D(); // restore the real camera after the reflection pass

      // 0. Fullscreen background layers (cloudsbg etc.), no depth
      const bgLayers = sceneData.bgLayers || [];
      if (bgLayers.length > 0) {
        gl.disable(gl.DEPTH_TEST);
        gl.disable(gl.BLEND);
        for (const layer of bgLayers) {
          if ((layer.shader || '') === 'cloudsbg') drawCloudsBgLayer(layer, elapsed, width, height);
        }
        gl.enable(gl.DEPTH_TEST);
        bindProg3D(); // drawCloudsBgLayer switched the bound program
      }

      // 1. Skybox / Dome: render first, no depth write
      gl.depthMask(false);
      gl.uniform1i(gl.getUniformLocation(prog3D, 'u_hasReflTex'), 0);
      for (const model of skyboxModels) {
        for (const mesh of model.meshes) drawMesh(model, mesh, { skybox: true });
      }
      for (const model of domeModels) {
        for (const mesh of model.meshes) drawMesh(model, mesh, { dome: true });
      }
      gl.depthMask(true);

      // 2. Opaque parts (bg shader meshes render as fullscreen background)
      gl.disable(gl.BLEND);
      for (const model of opaqueModels) {
        for (const mesh of model.meshes) {
          const isBody = Boolean(isCarScene && mesh.texUrl && mesh.texUrl.includes('body'));
          const isGlass = Boolean(mesh.texUrl && mesh.texUrl.includes('glass'));
          if (isGlass) {
            glassQueue.push({ model, mesh });
            continue;
          }
          drawMesh(model, mesh, { body: isBody, bg: (mesh.shader || '') === 'bg' });
        }
      }

      // 2b. Translucent overlays, far-to-near: neongrid floor first, then
      // bgfade/neonsun on top. Translucent passes never write depth so their
      // transparent pixels cannot occlude later geometry.
      if (translucentQueue.length > 0 || neonGridQueue.length > 0) {
        gl.enable(gl.BLEND);
        gl.depthMask(false);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        for (const { model, mesh } of neonGridQueue) {
          drawNeonGrid(model, mesh, proj3D, view3D, elapsed);
        }
        gl.useProgram(prog3D);
        for (const { model, mesh } of translucentQueue) {
          drawMesh(model, mesh, { bg: (mesh.shader || '') === 'bg', neonSun: (mesh.shader || '') === 'neonsun' });
        }
        gl.depthMask(true);
        gl.disable(gl.BLEND);
      }

      // 3. Shadow (blended)
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      for (const model of shadowModels) {
        for (const mesh of model.meshes) drawMesh(model, mesh, { shadow: true });
      }

      // 4. Grid floor with FBO reflection
      if (hasGrid && reflTex) {
        gl.activeTexture(gl.TEXTURE1);
        gl.bindTexture(gl.TEXTURE_2D, reflTex);
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_reflTex'), 1);
        gl.uniform1i(gl.getUniformLocation(prog3D, 'u_hasReflTex'), 1);
        gl.uniform2f(gl.getUniformLocation(prog3D, 'u_resolution'), width, height);
      }
      for (const model of gridModels) {
        for (const mesh of model.meshes) drawMesh(model, mesh, { grid: true });
      }
      gl.uniform1i(gl.getUniformLocation(prog3D, 'u_hasReflTex'), 0);

      // 5. Glass (blended)
      for (const { model, mesh } of glassQueue) {
        drawMesh(model, mesh, { glass: true });
      }

      // 6. Additive glow queue (jets, orbital effects, self-illuminated)
      // SRC_ALPHA, ONE: shaped by the shader's output alpha (aurora fade,
      // thunder sparkle); jets output alpha 1 so they behave as pure additive.
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
      for (const { model, mesh } of additiveQueue) {
        const shaderName = (mesh.shader || '').toLowerCase();
        const jetLike = shaderName.includes('jet') || (mesh.texUrl || '').toLowerCase().includes('jet');
        const isAurora = !jetLike && shaderName.includes('aurora');
        const isThunder = !jetLike && shaderName.includes('thunder');
        drawMesh(model, mesh, {
          jet: jetLike,
          aurora: isAurora,
          thunder: isThunder,
          selfIllum: !jetLike && !isAurora && !isThunder,
          followEye: isAurora || isThunder,
        });
      }

      // 7. 3D sprites (sun glow billboards) and particle streaks (starfield)
      const sprites3d = sceneData.sprites || [];
      const systems3d = sceneData.particles3d || [];
      if (sprites3d.length > 0 || systems3d.length > 0) {
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE); // additive, texture-alpha shaped
        for (const sp of sprites3d) {
          // View-space offset = camera-facing quad (WE sprite.vert semantics:
          // right*(u-0.5) + up*(v-0.5), scaled by 0.5 * object scale).
          const w = 0.5 * (sp.scale ? sp.scale[0] : 1);
          const h = 0.5 * (sp.scale ? sp.scale[1] : 1);
          drawBillboard(sp.origin, [w, 0], [0, h], sp.texUrl, [1, 1, 1, 1], proj3D, view3D);
        }
        for (const sys of systems3d) {
          if (!isPaused) updateParticles3d(sys, dt);
          const st = getParticles3d(sys);
          for (const p of st.list) {
            const fade = Math.min(1, Math.min(p.life, p.maxLife - p.life) / (0.2 * p.maxLife));
            // Streak: stretch the quad along the view-space velocity.
            const vv = [
              view3D[0] * p.vx + view3D[4] * p.vy + view3D[8] * p.vz,
              view3D[1] * p.vx + view3D[5] * p.vy + view3D[9] * p.vz,
            ];
            const speed = Math.hypot(vv[0], vv[1]);
            const halfLen = p.size * 0.5 * (1 + Math.min(speed * 0.08, 4));
            const halfWid = p.size * 0.5;
            let px = 1, py = 0;
            if (speed > 0.001) { px = vv[0] / speed; py = vv[1] / speed; }
            const axisX = [px * halfLen * 2, py * halfLen * 2];
            const axisY = [-py * halfWid * 2, px * halfWid * 2];
            drawBillboard([p.x, p.y, p.z], axisX, axisY, sys.texUrl, [p.color[0], p.color[1], p.color[2], fade], proj3D, view3D);
          }
        }
      }

      gl.disable(gl.DEPTH_TEST);
      gl.disable(gl.CULL_FACE);
      gl.disable(gl.BLEND);
      requestAnimationFrame(render);
      return;
    }

    const sceneW = sceneData.width || 3840;
    const sceneH = sceneData.height || 2160;

    let scale = 1;
    if (fitMode === 'cover') {
      scale = Math.max(width / sceneW, height / sceneH);
    } else if (fitMode === 'contain') {
      scale = Math.min(width / sceneW, height / sceneH);
    } // fill: viewport covers the whole canvas (non-uniform stretch)

    const vpW = fitMode === 'fill' ? width : Math.round(sceneW * scale);
    const vpH = fitMode === 'fill' ? height : Math.round(sceneH * scale);
    const vpX = fitMode === 'fill' ? 0 : Math.round((width - vpW) / 2);
    const vpY = fitMode === 'fill' ? 0 : Math.round((height - vpH) / 2);

    ensureFbo(Math.min(sceneW, 2048), Math.min(sceneH, 1080));

    // Projection matrix mapping scene coords (0..sceneW, 0..sceneH) to clip space (-1..1)
    const proj = mat4Ortho(0, sceneW, 0, sceneH, -1000, 1000);

    // Pass 1: Render background and sky layers into FBO for reflections
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.viewport(0, 0, fboWidth, fboHeight);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);

    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    gl.useProgram(progBasic);
    gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
    const aPos = gl.getAttribLocation(progBasic, 'a_pos');
    const aUv = gl.getAttribLocation(progBasic, 'a_uv');
    gl.enableVertexAttribArray(aPos);
    gl.enableVertexAttribArray(aUv);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 16, 0);
    gl.vertexAttribPointer(aUv, 2, gl.FLOAT, false, 16, 8);

    gl.uniformMatrix4fv(gl.getUniformLocation(progBasic, 'u_proj'), false, proj);
    gl.uniform1f(gl.getUniformLocation(progBasic, 'u_time'), elapsed);
    gl.uniform4f(gl.getUniformLocation(progBasic, 'u_uvRect'), 0, 0, 1, 1);
    gl.uniform1f(gl.getUniformLocation(progBasic, 'u_bright'), 1);
    gl.uniform1f(gl.getUniformLocation(progBasic, 'u_power'), 1);

    // Render sky & upper layers into FBO
    for (const layer of sceneData.layers) {
      if (layer.isGround || layer.isReflection) continue;
      // 文字只进屏幕通道：水面反射里叠一份时钟不是这场景的表现。
      if (layer.kind === 'text') continue;
      if (layer.mesh) {
        drawBasicMeshLayer(layer, aPos, aUv, sceneW, sceneH, elapsed, proj);
        continue;
      }
      const texRec = loadTexture(layer.texUrl, false, !!layer.videoTex);
      if (!texRec.loaded) continue;

      const origin = layerFrame(layer, sceneW, sceneH);
      const model = mat4Transform2D(origin[0], origin[1], layer.w, layer.h, origin[2]);
      gl.uniformMatrix4fv(gl.getUniformLocation(progBasic, 'u_model'), false, model);
      gl.uniform1f(gl.getUniformLocation(progBasic, 'u_alpha'), layer.alpha != null ? layer.alpha : 1.0);
      gl.uniform3f(gl.getUniformLocation(progBasic, 'u_tint'), 1, 1, 1);
      gl.uniform1f(gl.getUniformLocation(progBasic, 'u_sway'), layer.sway || 0);
      gl.uniform1f(gl.getUniformLocation(progBasic, 'u_sway_speed'), layer.swaySpeed || 1.0);

      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, texRec.texture);
      gl.uniform1i(gl.getUniformLocation(progBasic, 'u_tex'), 0);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    }

    // Pass 2: Render to screen viewport
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(vpX, vpY, vpW, vpH);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);

    // Render all layers (Sky -> Ground -> Reflection -> Particles)
    for (const layer of sceneData.layers) {
      if (layer.isReflection) {
        // Water Reflection Pass
        const maskRec = loadTexture(layer.texUrl);
        if (!maskRec.loaded) continue;

        gl.useProgram(progReflection);
        gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
        const rPos = gl.getAttribLocation(progReflection, 'a_pos');
        const rUv = gl.getAttribLocation(progReflection, 'a_uv');
        gl.enableVertexAttribArray(rPos);
        gl.enableVertexAttribArray(rUv);
        gl.vertexAttribPointer(rPos, 2, gl.FLOAT, false, 16, 0);
        gl.vertexAttribPointer(rUv, 2, gl.FLOAT, false, 16, 8);

        // Draw the reflection quad at the layer's own rect (fullscreen for
        // legacy scene-wide reflection layers).
        const rFrame = layerFrame(layer, sceneW, sceneH);
        const model = mat4Transform2D(rFrame[0], rFrame[1], layer.w, layer.h, rFrame[2]);
        gl.uniformMatrix4fv(gl.getUniformLocation(progReflection, 'u_proj'), false, proj);
        gl.uniformMatrix4fv(gl.getUniformLocation(progReflection, 'u_model'), false, model);
        gl.uniform4f(gl.getUniformLocation(progReflection, 'u_uvRect'), 0, 0, 1, 1);
        gl.uniform1f(gl.getUniformLocation(progReflection, 'u_time'), elapsed);
        gl.uniform1f(gl.getUniformLocation(progReflection, 'u_alpha'), 0.85);

        // Scene-uv rect of the quad (scene v grows downward, 0 at the top).
        const rectLeftU = (layer.x - layer.w / 2) / sceneW;
        const rectTopV = 1 - (layer.y + layer.h / 2) / sceneH;
        gl.uniform4f(gl.getUniformLocation(progReflection, 'u_rect'),
          rectLeftU, rectTopV, layer.w / sceneW, layer.h / sceneH);
        // Water line follows the scene data when the parser resolved one;
        // otherwise keep the legacy 0.65 / 0.42 / 0.38 window.
        const waterLine = typeof layer.waterLine === 'number' ? layer.waterLine : 0.65;
        const depthScale = (1 - waterLine) / 0.35;
        gl.uniform1f(gl.getUniformLocation(progReflection, 'u_waterLine'), waterLine);
        gl.uniform2f(gl.getUniformLocation(progReflection, 'u_reflectRange'),
          waterLine - 0.23 * depthScale, 0.38 * depthScale);

        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, fboTex);
        gl.uniform1i(gl.getUniformLocation(progReflection, 'u_fbo'), 0);

        gl.activeTexture(gl.TEXTURE1);
        gl.bindTexture(gl.TEXTURE_2D, maskRec.texture);
        gl.uniform1i(gl.getUniformLocation(progReflection, 'u_mask'), 1);

        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        continue;
      }

      // WE flowimage layer (flowing nebula): mask + 3 cross-fading layers.
      // All four textures are sampled with the plain quad UV (WE stretches
      // mask and content over the whole quad); served PNGs are already
      // cropped to the image rect, and clamp wrapping matches clampuvs.
      if (layer.shader === 'flowimage' && layer.texUrls && layer.texUrls.length >= 4) {
        const recs = layer.texUrls.slice(0, 4).map((u) => loadTexture(u));
        if (!recs.every((r) => r.loaded)) continue;
        gl.useProgram(progFlow);
        gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
        const fPos = gl.getAttribLocation(progFlow, 'a_pos');
        const fUv = gl.getAttribLocation(progFlow, 'a_uv');
        gl.enableVertexAttribArray(fPos);
        gl.enableVertexAttribArray(fUv);
        gl.vertexAttribPointer(fPos, 2, gl.FLOAT, false, 16, 0);
        gl.vertexAttribPointer(fUv, 2, gl.FLOAT, false, 16, 8);
        const origin = layerFrame(layer, sceneW, sceneH);
        const model = mat4Transform2D(origin[0], origin[1], layer.w, layer.h, origin[2]);
        gl.uniformMatrix4fv(gl.getUniformLocation(progFlow, 'u_proj'), false, proj);
        gl.uniformMatrix4fv(gl.getUniformLocation(progFlow, 'u_model'), false, model);
        const fcrop = layer.uvCrop || [0, 0, 1, 1];
        gl.uniform4f(gl.getUniformLocation(progFlow, 'u_uvRect'), fcrop[0], fcrop[1], fcrop[2], fcrop[3]);
        gl.uniform1f(gl.getUniformLocation(progFlow, 'u_time'), elapsed);
        const nums = layer.nums || {};
        gl.uniform3f(gl.getUniformLocation(progFlow, 'u_speeds'),
          nums.Speed0 ?? 0.01, nums.Speed1 ?? 0.01, nums.Speed2 ?? 0.01);
        gl.uniform1f(gl.getUniformLocation(progFlow, 'u_amp'), nums.Amount ?? 1);
        gl.uniform1f(gl.getUniformLocation(progFlow, 'u_bright'), nums.Bright ?? 1);
        const units = ['u_mask', 'u_l1', 'u_l2', 'u_l3'];
        for (let ui = 0; ui < 4; ui++) {
          gl.activeTexture(gl.TEXTURE0 + ui);
          gl.bindTexture(gl.TEXTURE_2D, recs[ui].texture);
          gl.uniform1i(gl.getUniformLocation(progFlow, units[ui]), ui);
        }
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        gl.activeTexture(gl.TEXTURE0);
        continue;
      }

      // WE flag layer (rippling tinted cloth): eagleflag
      if (layer.shader === 'flag' && layer.texUrls && layer.texUrls.length >= 3) {
        const recs = layer.texUrls.slice(0, 3).map((u) => loadTexture(u, true));
        if (!recs.every((r) => r.loaded)) continue;
        gl.useProgram(progFlag);
        gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
        const flPos = gl.getAttribLocation(progFlag, 'a_pos');
        const flUv = gl.getAttribLocation(progFlag, 'a_uv');
        gl.enableVertexAttribArray(flPos);
        gl.enableVertexAttribArray(flUv);
        gl.vertexAttribPointer(flPos, 2, gl.FLOAT, false, 16, 0);
        gl.vertexAttribPointer(flUv, 2, gl.FLOAT, false, 16, 8);
        const origin = layerFrame(layer, sceneW, sceneH);
        const model = mat4Transform2D(origin[0], origin[1], layer.w, layer.h, origin[2]);
        gl.uniformMatrix4fv(gl.getUniformLocation(progFlag, 'u_proj'), false, proj);
        gl.uniformMatrix4fv(gl.getUniformLocation(progFlag, 'u_model'), false, model);
        const flcrop = layer.uvCrop || [0, 0, 1, 1];
        gl.uniform4f(gl.getUniformLocation(progFlag, 'u_uvRect'), flcrop[0], flcrop[1], flcrop[2], flcrop[3]);
        gl.uniform1f(gl.getUniformLocation(progFlag, 'u_time'), elapsed);
        const fnums = layer.nums || {};
        gl.uniform1f(gl.getUniformLocation(progFlag, 'u_speed'), fnums.Speed ?? 0.4);
        gl.uniform1f(gl.getUniformLocation(progFlag, 'u_strength'), fnums.Strength ?? 0.5);
        const fcols = layer.userColors || {};
        const fc1 = fcols.color1 || [0, 0, 0];
        const fc2 = fcols.color2 || [0, 0, 0];
        const fc3 = fcols.color3 || [1, 1, 1];
        gl.uniform3f(gl.getUniformLocation(progFlag, 'u_color1'), fc1[0], fc1[1], fc1[2]);
        gl.uniform3f(gl.getUniformLocation(progFlag, 'u_color2'), fc2[0], fc2[1], fc2[2]);
        gl.uniform3f(gl.getUniformLocation(progFlag, 'u_color3'), fc3[0], fc3[1], fc3[2]);
        const funits = ['u_tex', 'u_normal', 'u_cloth'];
        for (let ui = 0; ui < 3; ui++) {
          gl.activeTexture(gl.TEXTURE0 + ui);
          gl.bindTexture(gl.TEXTURE_2D, recs[ui].texture);
          gl.uniform1i(gl.getUniformLocation(progFlag, funits[ui]), ui);
        }
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        gl.activeTexture(gl.TEXTURE0);
        continue;
      }

      // Puppet mesh layer (rigged character): MDLV triangles over the atlas.
      if (layer.mesh) {
        gl.useProgram(progBasic);
        if (!loadTexture(layer.texUrl).loaded) continue;
        drawBasicMeshLayer(layer, aPos, aUv, sceneW, sceneH, elapsed, proj);
        continue;
      }

      // 文字图层：清单里已带 fontSize / color / time，就地光栅化后走同一套 progBasic。
      if (layer.kind === 'text') {
        drawTextLayer(layer, aPos, aUv, sceneW, sceneH, wallClock, now, proj);
        continue;
      }

      // WE 的 waterflow 效果（本包＝白天/夜晚的云层流动）：单张内容图 + 流场遮罩 + 相位图。
      // 位移量按「strength 像素 / 内容贴图宽」归一到 UV——strength 是 WE 编辑器里的像素档，
      // 直接当 UV 用会把天空抹成一片（真机实锤：progFlow 的 amp·0.1·cycle 让 5 变成半屏位移）。
      // 环绕在着色器里用 fract 做：WebGL1 的 NPOT 贴图设成 REPEAT 会让整张纹理变成
      // incomplete，取样恒返回 (0,0,0,1) ⇒ 天空被画成一块不透明白痴黑（真机截图实锤，
      // 本包天空是 4200x2500，非 2 的幂）。纹理按 CLAMP 上传，越界部分由 fract 折回对边。
      // 遮罩或本层贴图没到位就落回标准绘制——少一次扰动，但天还在。
      if (layer.flow && layer.flow.enabled !== false) {
        const flowContent = loadTexture(layer.texUrl, false, !!layer.videoTex);
        const flowMask = loadTexture(layer.flow.maskUrl);
        if (flowContent.loaded && flowMask.loaded) {
          const flowPhase = layer.flow.phaseUrl ? loadTexture(layer.flow.phaseUrl) : null;
          gl.useProgram(progWaterflow);
          gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
          const fwPos = gl.getAttribLocation(progWaterflow, 'a_pos');
          const fwUv = gl.getAttribLocation(progWaterflow, 'a_uv');
          gl.enableVertexAttribArray(fwPos);
          gl.enableVertexAttribArray(fwUv);
          gl.vertexAttribPointer(fwPos, 2, gl.FLOAT, false, 16, 0);
          gl.vertexAttribPointer(fwUv, 2, gl.FLOAT, false, 16, 8);
          const flowOrigin = layerFrame(layer, sceneW, sceneH);
          const flowModel = mat4Transform2D(flowOrigin[0], flowOrigin[1], layer.w, layer.h, flowOrigin[2]);
          gl.uniformMatrix4fv(gl.getUniformLocation(progWaterflow, 'u_proj'), false, proj);
          gl.uniformMatrix4fv(gl.getUniformLocation(progWaterflow, 'u_model'), false, flowModel);
          const flowCrop = layer.uvCrop || [0, 0, 1, 1];
          gl.uniform4f(gl.getUniformLocation(progWaterflow, 'u_uvRect'), flowCrop[0], flowCrop[1], flowCrop[2], flowCrop[3]);
          const fwNums = layer.nums || {};
          const flowAmp = (layer.flow.strength || 1) / Math.max(1, flowContent.width);
          gl.uniform1f(gl.getUniformLocation(progWaterflow, 'u_time'), elapsed);
          gl.uniform1f(gl.getUniformLocation(progWaterflow, 'u_speed'), layer.flow.speed || 0.03);
          gl.uniform1f(gl.getUniformLocation(progWaterflow, 'u_amp'), flowAmp);
          gl.uniform1f(gl.getUniformLocation(progWaterflow, 'u_phasescale'), layer.flow.phasescale || 1);
          gl.uniform3f(gl.getUniformLocation(progWaterflow, 'u_tint'), 1, 1, 1);
          gl.uniform1f(gl.getUniformLocation(progWaterflow, 'u_bright'), fwNums.Bright ?? 1);
          gl.uniform1f(gl.getUniformLocation(progWaterflow, 'u_power'), fwNums.Power ?? 1);
          gl.uniform1f(gl.getUniformLocation(progWaterflow, 'u_alpha'), layer.alpha != null ? layer.alpha : 1.0);
          gl.activeTexture(gl.TEXTURE0);
          gl.bindTexture(gl.TEXTURE_2D, flowContent.texture);
          gl.uniform1i(gl.getUniformLocation(progWaterflow, 'u_tex'), 0);
          gl.activeTexture(gl.TEXTURE1);
          gl.bindTexture(gl.TEXTURE_2D, flowMask.texture);
          gl.uniform1i(gl.getUniformLocation(progWaterflow, 'u_mask'), 1);
          gl.activeTexture(gl.TEXTURE2);
          gl.bindTexture(gl.TEXTURE_2D, flowPhase && flowPhase.loaded ? flowPhase.texture : whiteTex());
          gl.uniform1i(gl.getUniformLocation(progWaterflow, 'u_phase'), 2);
          gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
          gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
          gl.activeTexture(gl.TEXTURE0);
          continue;
        }
      }

      // Standard Layer
      const texRec = loadTexture(layer.texUrl, false, !!layer.videoTex);
      if (!texRec.loaded) continue;

      gl.useProgram(progBasic);
      gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
      gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 16, 0);
      gl.vertexAttribPointer(aUv, 2, gl.FLOAT, false, 16, 8);

      const crop = layer.uvCrop || [0, 0, 1, 1];
      gl.uniform4f(gl.getUniformLocation(progBasic, 'u_uvRect'), crop[0], crop[1], crop[2], crop[3]);
      const lnums = layer.nums || {};
      gl.uniform1f(gl.getUniformLocation(progBasic, 'u_bright'), lnums.Bright ?? 1);
      gl.uniform1f(gl.getUniformLocation(progBasic, 'u_power'), lnums.Power ?? 1);

      const origin = layerFrame(layer, sceneW, sceneH);
      const model = mat4Transform2D(origin[0], origin[1], layer.w, layer.h, origin[2]);
      gl.uniformMatrix4fv(gl.getUniformLocation(progBasic, 'u_proj'), false, proj);
      gl.uniformMatrix4fv(gl.getUniformLocation(progBasic, 'u_model'), false, model);
      gl.uniform1f(gl.getUniformLocation(progBasic, 'u_time'), elapsed);
      gl.uniform1f(gl.getUniformLocation(progBasic, 'u_alpha'), layer.alpha != null ? layer.alpha : 1.0);
      gl.uniform3f(gl.getUniformLocation(progBasic, 'u_tint'), 1, 1, 1);
      gl.uniform1f(gl.getUniformLocation(progBasic, 'u_sway'), layer.sway || 0);
      gl.uniform1f(gl.getUniformLocation(progBasic, 'u_sway_speed'), layer.swaySpeed || 1.0);

      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, texRec.texture);
      gl.uniform1i(gl.getUniformLocation(progBasic, 'u_tex'), 0);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    }

    // Render Particles (Shooting Stars, Fireflies)
    if (activeParticles.length > 0) {
      gl.useProgram(progParticle);
      gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
      const pPos = gl.getAttribLocation(progParticle, 'a_pos');
      const pUv = gl.getAttribLocation(progParticle, 'a_uv');
      gl.enableVertexAttribArray(pPos);
      gl.enableVertexAttribArray(pUv);
      gl.vertexAttribPointer(pPos, 2, gl.FLOAT, false, 16, 0);
      gl.vertexAttribPointer(pUv, 2, gl.FLOAT, false, 16, 8);
      gl.uniformMatrix4fv(gl.getUniformLocation(progParticle, 'u_proj'), false, proj);
      gl.uniform4f(gl.getUniformLocation(progParticle, 'u_uvRect'), 0, 0, 1, 1);

      gl.blendFunc(gl.SRC_ALPHA, gl.ONE); // Additive luminous particles

      for (const p of activeParticles) {
        const progress = p.life / p.maxLife;
        const alpha = Math.sin(progress * Math.PI); // Fade in & out
        const texRec = p.system.texUrl ? loadTexture(p.system.texUrl) : null;
        if (texRec && texRec.loaded) {
          gl.activeTexture(gl.TEXTURE0);
          gl.bindTexture(gl.TEXTURE_2D, texRec.texture);
          gl.uniform1i(gl.getUniformLocation(progParticle, 'u_tex'), 0);
        }

        // Draw trail if meteor
        if (p.trail && p.trail.length > 1) {
          for (let ti = 0; ti < p.trail.length; ti++) {
            const tp = p.trail[ti];
            const tRatio = (ti + 1) / p.trail.length;
            const tAlpha = alpha * tRatio * 0.6;
            const tModel = mat4Transform2D(tp.x, tp.y, p.size * tRatio * 1.5, p.size * 0.4, Math.atan2(p.vy, p.vx));
            gl.uniformMatrix4fv(gl.getUniformLocation(progParticle, 'u_model'), false, tModel);
            gl.uniform4f(gl.getUniformLocation(progParticle, 'u_color'), p.color[0], p.color[1], p.color[2], tAlpha);
            gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
          }
        }

        const model = mat4Transform2D(p.x, p.y, p.size * (p.system.type === 'meteor' ? 3 : 1), p.size, Math.atan2(p.vy, p.vx));
        gl.uniformMatrix4fv(gl.getUniformLocation(progParticle, 'u_model'), false, model);
        gl.uniform4f(gl.getUniformLocation(progParticle, 'u_color'), p.color[0], p.color[1], p.color[2], alpha * p.color[3]);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      }
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    }

    // WE 粒子系统（雾 / 光束 / 鸟 / 星星）：走 2D 正交通道，画在图层与程序化流星之上。
    // 混合场景（3D 且有模型）不在这里画：上面 3D 分支已经用同一份 particles3d 跑过
    // updateParticles3d，两条通道都画会变成双倍数量 + 一份贴平面的雾。
    const drewParticles3d = sceneData.is3D && sceneData.models && sceneData.models.length > 0;
    const weSystems = drewParticles3d ? [] : (sceneData.particles3d || []);
    // 观测现场（不参与任何渲染判定）：每帧汇总各系统存活数、绘制的 quad 数与推进过的模拟帧。
    const particles2dLive = {};
    let particles2dQuads = 0;
    if (weSystems.length > 0) {
      gl.useProgram(progParticle);
      gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
      const wPos = gl.getAttribLocation(progParticle, 'a_pos');
      const wUv = gl.getAttribLocation(progParticle, 'a_uv');
      gl.enableVertexAttribArray(wPos);
      gl.enableVertexAttribArray(wUv);
      gl.vertexAttribPointer(wPos, 2, gl.FLOAT, false, 16, 0);
      gl.vertexAttribPointer(wUv, 2, gl.FLOAT, false, 16, 8);
      gl.uniformMatrix4fv(gl.getUniformLocation(progParticle, 'u_proj'), false, proj);
      const wTex = gl.getUniformLocation(progParticle, 'u_tex');
      const wRect = gl.getUniformLocation(progParticle, 'u_uvRect');
      const wColor = gl.getUniformLocation(progParticle, 'u_color');
      const wModel = gl.getUniformLocation(progParticle, 'u_model');
      gl.activeTexture(gl.TEXTURE0);
      for (const sys of weSystems) {
        const rec = sys.texUrl ? loadTexture(sys.texUrl) : null;
        // 贴图缺席（没装 WE、内置资产取不到）就整条系统跳过：清单里的键已由主线程
        // 按 builtInAssetKeys 撤掉，这里不额外画东西。
        if (!rec || !rec.loaded) continue;
        const st = getParticles2d(sys);
        if (!st.warm) {
          // 首帧预热到稳态：WE 的桌面里雾与光束从来不是一片空。
          // 步数按「活满一整个生命周期 + 至少生出第一只」算——光束速率 0.3/s、寿命 8~20 s，
          // 固定 3 秒预热连一只都生不出来（预热不足实测 live=0，画面上没有光束）。
          st.warm = true;
          const warmRate = Math.max(0.01, nz(sys.rate, 0));
          const warmSeconds = Math.min(40, Math.max(3, nz(sys.lifeMax, 4) + 1 / warmRate + 0.5));
          for (let i = 0; i < Math.ceil(warmSeconds * 30); i++) updateParticles2d(sys, st, 1 / 30);
        }
        if (!isPaused) updateParticles2d(sys, st, dt);
        particles2dLive[sys.name] = st.list.length;
        if (st.list.length === 0) continue;
        gl.bindTexture(gl.TEXTURE_2D, rec.texture);
        gl.uniform1i(wTex, 0);
        if (sys.blend === 'translucent') gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        else gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
        const sc = sys.sizeScale || [1, 1];
        const fr = sys.frames;
        const cols = fr && fr.w > 0 ? Math.max(1, Math.floor(rec.width / fr.w)) : 1;
        const rows = fr && fr.h > 0 ? Math.max(1, Math.floor(rec.height / fr.h)) : 1;
        // 贴图长宽比：有序列帧按「一格」算，否则按整张（对应 WE 的 textureRatio）。
        const ratio = fr && fr.w > 0 && fr.h > 0
          ? fr.h / fr.w
          : rec.height / Math.max(1, rec.width);
        const fadeInDef = 0.1, fadeOutDef = 0.1;
        for (const p of st.list) {
          let k = 1;
          const fi = sys.fadeIn !== undefined ? sys.fadeIn : fadeInDef * p.maxLife;
          const fo = sys.fadeOut !== undefined ? sys.fadeOut : fadeOutDef * p.maxLife;
          if (fi > 0) k = Math.min(k, p.life / fi);
          if (fo > 0) k = Math.min(k, (p.maxLife - p.life) / fo);
          if (sys.oscFreq) {
            const lo = nz(sys.oscMin, 0);
            const wave = 0.5 + 0.5 * Math.sin((elapsed * nz(sys.oscFreq, 1) + p.osc) * 6.28318);
            k *= lo + (1 - lo) * wave;
          }
          const a = Math.min(1, Math.max(0, p.alpha * k));
          if (a <= 0) continue;
          // WE 的精灵是「宽 = size、高 = size × 贴图长宽比」的等比四边形（几何着色器里
          // textureRatio 乘在 up 上），对象缩放再各轴乘一遍；不做画布封顶——
          // 官方雾预设就是比画面还大的软团，封顶会把它压成一块带硬边的屏斑。
          const w = p.size * nz(sc[0], 1);
          const h = p.size * nz(sc[1], 1) * ratio;
          let u0 = 0, v0 = 0, u1 = 1, v1 = 1;
          if (fr && fr.count > 1) {
            // 序列帧按「粒子年龄」推进（WE 传进着色器的就是 frac(lifetime) × 帧数），
            // 起点再按 phase 错开，一群鸟才不会齐刷刷新翅膀。
            const idx = Math.floor(p.phase * fr.count + p.life * fr.count) % fr.count;
            const cx = idx % cols;
            const cy = Math.floor(idx / cols) % rows;
            u0 = (cx * fr.w) / rec.width;
            v0 = (cy * fr.h) / rec.height;
            u1 = ((cx + 1) * fr.w) / rec.width;
            v1 = ((cy + 1) * fr.h) / rec.height;
          }
          // 镜像靠交换 uv 边界做：vsBasic 算的是 xy + a_uv*(zw − xy)，负跨度即翻采样。
          // 真机 day2 的鸟群 scale.x = −1，作者用它把朝左的贴图翻成与飞行方向一致。
          gl.uniform4f(wRect,
            sys.flipX ? u1 : u0, sys.flipY ? v1 : v0,
            sys.flipX ? u0 : u1, sys.flipY ? v0 : v1);
          gl.uniformMatrix4fv(wModel, false,
            mat4Transform2D(p.x, p.y, w, h, nz(sys.angle, 0) + p.rot));
          gl.uniform4f(wColor, p.color[0], p.color[1], p.color[2], a);
          gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
          particles2dQuads += 1;
        }
      }
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      if (!isPaused) particles2dSteps += 1;
    }
    // 观测现场（不参与任何渲染判定）：区分「没发射」「贴图没加载上」「模拟没推进」三层。
    window.__weParticles = {
      systems: weSystems.length,
      live: particles2dLive,
      quads: particles2dQuads,
      steps: particles2dSteps,
    };

    requestAnimationFrame(render);
  }

  // Crash guard: a render exception must not freeze the wallpaper silently.
  function render(now) {
    try {
      if (contextLost) { requestAnimationFrame(render); return; }
      renderFrame(now);
    } catch (e) {
      if (!window.__weRenderErr) {
        window.__weRenderErr = 1;
        console.error('we-scene-player render error:', e && e.stack || String(e));
      }
      requestAnimationFrame(render);
    }
  }

  canvas.addEventListener('webglcontextlost', (event) => {
    event.preventDefault();
    contextLost = true;
  });
  canvas.addEventListener('webglcontextrestored', () => {
    // WebGL objects are invalid after restoration. Ask the embedding
    // controller to rebuild this isolated renderer instead of drawing with
    // stale programs/textures.
    window.parent.postMessage({ type: 'dsh-scene-needs-reload' }, window.location.origin);
  });

  // Load manifest
  const token = window.location.pathname.split('/').filter(Boolean).pop();
  fetch('/wallpaper-engine/scene-manifest/' + token)
    .then(res => res.json())
    .then(data => {
      if (data.ok && data.manifest) {
        // A hot-swapped preset that arrived while this fetch was in flight wins:
        // it already reflects the user's current (newer) values.
        if (!manifestOverridden) sceneData = data.manifest;
      } else {
        // Manifest could not be built: let the embedding parent degrade to the
        // wallpaper's static frame instead of showing a blank canvas.
        try { window.parent.postMessage({ type: 'dsh-scene-unavailable' }, window.location.origin); } catch (e) {}
      }
    })
    .catch(err => {
      console.error('Failed to load scene manifest', err);
      try { window.parent.postMessage({ type: 'dsh-scene-unavailable' }, window.location.origin); } catch (e) {}
    });

  // Listen for controller messages; only the embedding parent on the same
  // origin may steer the player.
  window.addEventListener('message', (ev) => {
    if (ev.source !== window.parent || ev.origin !== window.location.origin) return;
    const msg = ev.data;
    if (!msg || typeof msg !== 'object') return;
    if (msg.type === 'dsh-set-fit' && msg.fit) {
      fitMode = msg.fit;
    } else if (msg.type === 'dsh-set-pause') {
      const wasPaused = isPaused;
      isPaused = !!msg.paused;
      if (wasPaused && !isPaused) fpsState.clear();
      // 暂停必须真的停掉视频解码，否则「已暂停」的壁纸仍在后台吃 CPU/GPU。
      for (const rec of textureCache.values()) {
        if (!rec.video) continue;
        if (isPaused) {
          rec.video.pause();
        } else {
          const started = rec.video.play();
          if (started && typeof started.catch === 'function') started.catch(function () {});
        }
      }
    } else if (msg.type === 'dsh-set-manifest' && msg.manifest && typeof msg.manifest === 'object') {
      manifestOverridden = true;
      swapSceneManifest(msg.manifest);
    } else if (msg.type === 'dsh-recover-renderer') {
      if (gl.isContextLost()) {
        const ext = gl.getExtension('WEBGL_lose_context');
        if (ext) ext.restoreContext();
        else window.parent.postMessage({ type: 'dsh-scene-needs-reload' }, window.location.origin);
      } else {
        // Force an immediate fresh frame after compositor/theme changes.
        renderFrame(performance.now());
      }
    }
  });

  requestAnimationFrame(render);
})();
</script>
</body>
</html>
`;
