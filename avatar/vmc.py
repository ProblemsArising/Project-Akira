"""
VMC / OSC avatar control for VSeeFace.

This version is intentionally conservative because VSeeFace receiver mode can
fight itself if several things control the same blendshapes/bones.

It handles:
- Text-based fake mouth visemes using a coordinated V5.5 face-frame sender.
- Persistent facial expressions using Joy/Fun/Sorrow/Angry/Surprised.
- A replayed standing VMC pose from the user's captured good pose, so the model
  does not sit in T-pose when tracking is disabled.
- V6.3 keyword-scored expression selection with smoother pose suppression: happy,
  playful, surprised, concerned, annoyed, and soft.

Important defaults:
- Blink/look idle is OFF because it caused eye glitching on this model.
- Body pose uses the captured arms-at-side pose instead of invented rotations.
- V6.3 keeps expression keywords in avatar/expression_keywords.py in the reply and only soft-suppresses idle during speech poses.
- V5.5 fix: mouth and expression blends are applied together from one loop to avoid VSeeFace hiccups.
- Neutral is not sent because forcing Neutral can fight expressions.
"""

from __future__ import annotations

import atexit
import math
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    from pythonosc.udp_client import SimpleUDPClient
except ImportError:  # Keep the assistant usable even if python-osc is missing.
    SimpleUDPClient = None

try:
    # Normal package import when running assistant.py from project root.
    from avatar.expression_keywords import (
        best_expression_score,
        expression_from_text,
        expression_keyword_scores,
    )
except ImportError:
    # Fallback for running this file directly from inside avatar/.
    from expression_keywords import (
        best_expression_score,
        expression_from_text,
        expression_keyword_scores,
    )


VSEEFACE_IP = "127.0.0.1"
VSEEFACE_PORT = 39539

# ---------------------------------------------------------------------------
# Mouth settings -- these match the values you said worked well.
# ---------------------------------------------------------------------------

VOWEL_BLENDS = ("A", "I", "U", "E", "O")
EXPRESSION_BLENDS = ("Joy", "Fun", "Sorrow", "Angry", "Surprised")

MOUTH_FPS = 28

# pyttsx3 -> VB Cable -> RVCC can add latency.
# Increase if the mouth moves before you hear the changed voice.
MOUTH_START_DELAY_SECONDS = 1.15

# Overall scale for mouth movement. Lower this if the mouth opens too wide.
MOUTH_SCALE = 0.95

# Small random variation to avoid looking identical every syllable.
RANDOM_AMOUNT = 0.08

# Mouth uses the V5.0/V5.1 behavior because that was the stable version on this model.
# It allows the small secondary vowel values from VISEME_SHAPES, but resets all
# vowel blends before each frame so they do not stack inside VSeeFace.
RESET_OTHER_VOWELS_EACH_FRAME = True

# How snappy mouth changes are.
# Higher attack = opens faster. Lower release = closes softer.
ATTACK_SPEED = 0.60
RELEASE_SPEED = 0.42

# ---------------------------------------------------------------------------
# Expression settings
# ---------------------------------------------------------------------------

ENABLE_EXPRESSIONS = True
ENABLE_IDLE_FACE = True
IDLE_FACE_FPS = 20

# V5.5: one coordinated blend/apply loop owns ALL face blendshapes.
# This avoids tiny hiccups caused by mouth and expression threads both calling /Blend/Apply.
FACE_BLEND_FPS = 120

# Keep values modest. VRM expressions can get intense fast.
IDLE_EXPRESSION_STRENGTH = 0.30
SPEAKING_EXPRESSION_STRENGTH = 0.72
EXPRESSION_FADE_SPEED = 0.08

# Leave these OFF for now. They were the most likely cause of the eye glitching.
ENABLE_IDLE_BLINKS = False
ENABLE_IDLE_GAZE = False

# ---------------------------------------------------------------------------
# Body / pose settings
# ---------------------------------------------------------------------------

AUTO_START_IDLE = True
ENABLE_STANDING_POSE_REPLAY = True
POSE_FPS = 18

# 1.0 is intentionally more visible than the previous 0.20.
# Lower to 0.50 if the body looks too busy. Set to 0.0 to disable motion.
BODY_IDLE_STRENGTH = 1.00

# Visible-but-safe idle movement on top of the captured standing pose.
BODY_ROOT_BOB_METERS = 0.012
BODY_SWAY_METERS = 0.010
BODY_BREATH_DEGREES = 2.20
BODY_HEAD_YAW_DEGREES = 2.40
BODY_ARM_SWAY_DEGREES = 1.80

# V6.2: while a speaking expression pose is active, reduce idle breathing/sway.
# Hard-freezing idle looked rhythmic, so this uses a tiny amount of motion instead of 0.
DISABLE_IDLE_DURING_EXPRESSIONS = True
IDLE_STRENGTH_DURING_EXPRESSIONS = 0.2

# Random idle expressions caused rhythmic body freezes in V6.1. Keep the body idle
# continuous unless Akira is actually reacting to a spoken reply.
ENABLE_RANDOM_IDLE_EXPRESSIONS = False

# ---------------------------------------------------------------------------
# V6.2 body pose / calibrated arm gesture settings
# ---------------------------------------------------------------------------

ENABLE_BODY_EXPRESSIONS = True

# Overall body-expression strength. This layers on top of BODY_IDLE_STRENGTH.
# Lower to 0.60 if gestures feel too dramatic, raise to 1.25 if too subtle.
BODY_EXPRESSION_STRENGTH = 1.00

# Makes the pose offsets more/less dramatic without changing idle breathing.
BODY_POSE_STRENGTH = 1.00

# How fast posture/emotion changes ease in/out.
BODY_EXPRESSION_FADE_SPEED = 0.055

# Extra movement while she is actively speaking.
BODY_SPEAKING_MOTION_BOOST = 0.28

# Speaking motion is a little more visible than pure idle.
BODY_TALK_PULSE_DEGREES = 1.35
BODY_TALK_HAND_METERS = 0.010

# V6.2 arm gesture tuning.
# Arms are driven by bone rotations only. Hand trackers are disabled because calibration showed VSeeFace ignores them.
# If arms are too dramatic, lower ARM_GESTURE_STRENGTH first.
ARM_GESTURE_STRENGTH = 1.00
ARM_TRACKER_STRENGTH = 0.00
ARM_BONE_ROTATION_STRENGTH = 1.00
ARM_SPEAKING_SWAY_METERS = 0.018
ARM_SPEAKING_LIFT_METERS = 0.014

# If exact bone replay does not move the body in VSeeFace, try setting this True.
# Some receiver setups respond better to the VMC tracker positions too.
SEND_TRACKER_POSITIONS = False

# Do not send eye bones for now. This avoids eye fighting/glitching.
SKIP_BONES = {"LeftEye", "RightEye"}


Vec3 = Tuple[float, float, float]
Quat = Tuple[float, float, float, float]


@dataclass
class VisemeEvent:
    blends: Dict[str, float]
    duration: float


VISEME_SHAPES: Dict[str, Dict[str, float]] = {
    "a": {"A": 0.85, "I": 0.00, "U": 0.00, "E": 0.05, "O": 0.00},
    "i": {"A": 0.12, "I": 0.72, "U": 0.00, "E": 0.10, "O": 0.00},
    "u": {"A": 0.04, "I": 0.00, "U": 0.78, "E": 0.00, "O": 0.12},
    "e": {"A": 0.22, "I": 0.10, "U": 0.00, "E": 0.72, "O": 0.00},
    "o": {"A": 0.10, "I": 0.00, "U": 0.18, "E": 0.00, "O": 0.78},
    "closed": {"A": 0.00, "I": 0.00, "U": 0.00, "E": 0.00, "O": 0.00},
    "small": {"A": 0.18, "I": 0.00, "U": 0.00, "E": 0.00, "O": 0.00},
}


EXPRESSION_PRESETS: Dict[str, Dict[str, float]] = {
    "neutral": {"Joy": 0.00, "Fun": 0.00, "Sorrow": 0.00, "Angry": 0.00, "Surprised": 0.00},
    "soft": {"Joy": 0.08, "Fun": 0.03, "Sorrow": 0.00, "Angry": 0.00, "Surprised": 0.00},
    "happy": {"Joy": 0.30, "Fun": 0.08, "Sorrow": 0.00, "Angry": 0.00, "Surprised": 0.00},
    "playful": {"Joy": 0.12, "Fun": 0.36, "Sorrow": 0.00, "Angry": 0.00, "Surprised": 0.00},
    "surprised": {"Joy": 0.03, "Fun": 0.02, "Sorrow": 0.00, "Angry": 0.00, "Surprised": 0.42},
    "concerned": {"Joy": 0.00, "Fun": 0.00, "Sorrow": 0.30, "Angry": 0.00, "Surprised": 0.05},
    "annoyed": {"Joy": 0.02, "Fun": 0.12, "Sorrow": 0.00, "Angry": 0.24, "Surprised": 0.00},
    "shy": {"Joy": 0.10, "Fun": 0.18, "Sorrow": 0.04, "Angry": 0.00, "Surprised": 0.02},
}

# Expression keyword detection lives in avatar/expression_keywords.py.
# Edit that file to add new trigger words without touching this VMC controller.


# V5.8 body pose presets.
# These are actual pose targets, not just "more/less wavy idle".  Values are
# still layered on top of the captured arms-at-side standing pose, so each pose
# remains stable and can fade smoothly back to idle.
BODY_EXPRESSION_KEYS = (
    "energy",
    "root_x_m", "root_y_m", "root_z_m",
    "root_bounce_m", "root_sway_m", "root_forward_m",
    "spine_pitch_deg", "spine_yaw_deg", "spine_roll_deg",
    "chest_pitch_deg", "chest_yaw_deg", "chest_roll_deg",
    "upper_chest_pitch_deg", "upper_chest_yaw_deg", "upper_chest_roll_deg",
    "neck_pitch_deg", "neck_yaw_deg", "neck_roll_deg",
    "head_pitch_deg", "head_yaw_deg", "head_roll_deg",
    "left_shoulder_pitch_deg", "left_shoulder_yaw_deg", "left_shoulder_roll_deg",
    "right_shoulder_pitch_deg", "right_shoulder_yaw_deg", "right_shoulder_roll_deg",
    "left_upperarm_pitch_deg", "left_upperarm_yaw_deg", "left_upperarm_roll_deg",
    "right_upperarm_pitch_deg", "right_upperarm_yaw_deg", "right_upperarm_roll_deg",
    "left_lowerarm_pitch_deg", "left_lowerarm_yaw_deg", "left_lowerarm_roll_deg",
    "right_lowerarm_pitch_deg", "right_lowerarm_yaw_deg", "right_lowerarm_roll_deg",
    "left_hand_pitch_deg", "left_hand_yaw_deg", "left_hand_roll_deg",
    "right_hand_pitch_deg", "right_hand_yaw_deg", "right_hand_roll_deg",
    "left_hand_x_m", "left_hand_y_m", "left_hand_z_m",
    "right_hand_x_m", "right_hand_y_m", "right_hand_z_m",
    # Old generic keys are kept for compatibility with earlier math.
    "shoulder_pitch_deg", "shoulder_roll_deg", "arm_pitch_deg", "arm_roll_deg",
    "hand_y_m", "hand_z_m",
)


def _blank_body_pose(**values: float) -> Dict[str, float]:
    pose = {key: 0.0 for key in BODY_EXPRESSION_KEYS}
    pose.update({key: float(value) for key, value in values.items()})
    return pose


BODY_EXPRESSION_PRESETS: Dict[str, Dict[str, float]] = {
    # Neutral cozy idle. Arms stay near the captured arms-at-side pose.
    "soft": _blank_body_pose(
        energy=0.00,
        head_pitch_deg=0.20,
        head_roll_deg=0.20,
    ),

    # Friendly/excited. Uses calibrated forward/up axes:
    # LeftUpperArm Y+ = left arm forward, RightUpperArm Y- = right arm forward.
    # LeftUpperArm Z- = left arm away/up, RightUpperArm Z+ = right arm away/up.
    "happy": _blank_body_pose(
        energy=0.60,
        root_y_m=0.006,
        root_z_m=-0.003,
        chest_pitch_deg=-0.80,
        upper_chest_pitch_deg=-1.10,
        head_pitch_deg=-0.70,
        head_roll_deg=0.50,

        left_upperarm_yaw_deg=42.00,
        left_upperarm_roll_deg=-16.00,
        right_upperarm_yaw_deg=-42.00,
        right_upperarm_roll_deg=16.00,

        left_lowerarm_yaw_deg=10.00,
        left_lowerarm_roll_deg=-8.00,
        right_lowerarm_yaw_deg=-10.00,
        right_lowerarm_roll_deg=8.00,
    ),

    # Playful/gamer-companion. V6.0 looked like a handshake, so V6.1
    # avoids the strong forward axis. It is mostly attitude lean + a low side arm.
    "playful": _blank_body_pose(
        energy=0.55,
        root_x_m=0.003,
        root_z_m=-0.001,
        spine_roll_deg=0.80,
        chest_roll_deg=1.20,
        upper_chest_roll_deg=1.40,
        head_yaw_deg=2.80,
        head_roll_deg=2.40,
        head_pitch_deg=-0.20,

        left_upperarm_yaw_deg=0.00,
        left_upperarm_roll_deg=-2.00,

        # Right arm comes slightly out to the side, not forward.
        right_upperarm_yaw_deg=-4.00,
        right_upperarm_roll_deg=18.00,
        right_lowerarm_yaw_deg=0.00,
        right_lowerarm_roll_deg=8.00,
        right_hand_roll_deg=3.00,
    ),

    # Surprised. V5.9 was too far back/exorcist; this is a smaller pop with
    # hands up but little spine/head arch.
    "surprised": _blank_body_pose(
        energy=0.85,
        root_y_m=0.014,
        root_z_m=0.004,
        spine_pitch_deg=-0.50,
        chest_pitch_deg=-1.20,
        upper_chest_pitch_deg=-1.50,
        neck_pitch_deg=-0.40,
        head_pitch_deg=-1.10,

        left_upperarm_yaw_deg=22.00,
        left_upperarm_roll_deg=-26.00,
        right_upperarm_yaw_deg=-22.00,
        right_upperarm_roll_deg=26.00,

        left_lowerarm_yaw_deg=6.00,
        left_lowerarm_roll_deg=-12.00,
        right_lowerarm_yaw_deg=-6.00,
        right_lowerarm_roll_deg=12.00,
    ),

    # Concerned/careful. V6.0 was just arms-forward and meh. V6.1 is
    # sad/soft: head down, slight slouch, arms kept near/behind the body.
    "concerned": _blank_body_pose(
        energy=-0.25,
        root_y_m=-0.007,
        root_z_m=0.001,
        spine_pitch_deg=0.70,
        chest_pitch_deg=1.30,
        upper_chest_pitch_deg=1.50,
        neck_pitch_deg=1.10,
        head_pitch_deg=3.20,
        head_yaw_deg=-0.60,
        head_roll_deg=-1.00,

        # Slightly behind/down instead of forward, so palms do not look weird.
        left_upperarm_yaw_deg=-8.00,
        left_upperarm_roll_deg=1.00,
        right_upperarm_yaw_deg=8.00,
        right_upperarm_roll_deg=-1.00,

        left_lowerarm_yaw_deg=-2.00,
        right_lowerarm_yaw_deg=2.00,
    ),

    # Mild attitude. Right-arm gesture, left arm mostly neutral so it does not
    # grab the tail.
    "annoyed": _blank_body_pose(
        energy=0.30,
        root_x_m=-0.003,
        spine_roll_deg=-0.70,
        chest_roll_deg=-1.30,
        upper_chest_roll_deg=-1.60,
        head_yaw_deg=-3.40,
        head_roll_deg=-2.20,
        head_pitch_deg=0.60,

        left_upperarm_yaw_deg=2.00,
        left_upperarm_roll_deg=-2.00,

        right_upperarm_yaw_deg=-38.00,
        right_upperarm_roll_deg=10.00,
        right_lowerarm_yaw_deg=-8.00,
        right_lowerarm_roll_deg=4.00,
    ),

    # Shy/embarrassed. This intentionally keeps the "arms slightly behind" vibe
    # you said could work for shy, but it is only used for shy/embarrassed text.
    "shy": _blank_body_pose(
        energy=-0.05,
        root_z_m=0.002,
        spine_pitch_deg=0.30,
        chest_pitch_deg=0.80,
        upper_chest_pitch_deg=1.00,
        head_pitch_deg=2.60,
        head_roll_deg=1.40,

        left_upperarm_yaw_deg=-18.00,
        left_upperarm_roll_deg=4.00,
        right_upperarm_yaw_deg=18.00,
        right_upperarm_roll_deg=-4.00,

        left_lowerarm_yaw_deg=-4.00,
        right_lowerarm_yaw_deg=4.00,
    ),
}


def _scale_body_expression(shape: Dict[str, float], strength: float) -> Dict[str, float]:
    """Scale a body pose, with calibrated arm-bone tuning for V6.2."""
    out: Dict[str, float] = {}
    for key in BODY_EXPRESSION_KEYS:
        value = float(shape.get(key, 0.0)) * float(strength)
        if "upperarm" in key or "lowerarm" in key or "shoulder" in key or "hand_" in key:
            value *= ARM_GESTURE_STRENGTH
        if key.endswith("_deg") and ("upperarm" in key or "lowerarm" in key or "shoulder" in key or "hand_" in key):
            value *= ARM_BONE_ROTATION_STRENGTH
        if key.endswith("_m") and "hand_" in key:
            value *= ARM_TRACKER_STRENGTH
        out[key] = value
    return out



# This is your captured known-good standing pose with arms at the side.
# It gets parsed at import time so the file stays editable.
STANDING_CAPTURE = r'''
ROOT root | pos=(0.0, 0.0, 0.0) | rot=(-0.0, -0.0, -0.0, 1.0)
BONE Hips | pos=(-0.0017851905431598425, 0.8962059617042542, 0.011255824938416481) | rot=(2.719457938837877e-07, 6.836042132363218e-08, 2.6854168666545775e-08, -1.0)
BONE LeftUpperLeg | pos=(-0.0679103434085846, -0.03734874725341797, -0.0034292396157979965) | rot=(-0.0010280493879690766, -0.046121835708618164, 0.006791056599467993, 0.9989122152328491)
BONE RightUpperLeg | pos=(0.0679103434085846, -0.03734874725341797, -0.0034292396157979965) | rot=(-0.0010278919944539666, 0.04612356051802635, -0.006791514344513416, 0.9989121556282043)
BONE LeftLowerLeg | pos=(9.313225746154785e-09, -0.35753312706947327, -0.009738607332110405) | rot=(-0.0025400915183126926, -0.0022319816052913666, 0.0009848589543253183, 0.9999938011169434)
BONE RightLowerLeg | pos=(-6.05359673500061e-09, -0.35753315687179565, -0.009738607332110405) | rot=(-0.0025400484446436167, 0.0022317878901958466, -0.0009848002810031176, 0.9999938607215881)
BONE LeftFoot | pos=(-2.3283064365386963e-10, -0.4128904640674591, -0.022791825234889984) | rot=(-0.0005324308876879513, 0.04580018296837807, 0.0061761378310620785, 0.9989314079284668)
BONE RightFoot | pos=(-1.0477378964424133e-09, -0.4128904938697815, -0.022791830822825432) | rot=(-0.0005325707024894655, -0.045802295207977295, -0.006176631432026625, 0.9989312887191772)
BONE Spine | pos=(5.713845879212852e-32, 0.04664790630340576, 0.011488143354654312) | rot=(2.0861622829215776e-07, 1.1920927533992653e-07, -3.8272506095327757e-16, 1.0)
BONE Chest | pos=(-1.935371353674815e-17, 0.09854346513748169, 0.001662861555814743) | rot=(-0.0, -0.0, -0.0, 1.0)
BONE Neck | pos=(8.767018644186583e-17, 0.1246337890625, -0.036136891692876816) | rot=(1.4210854715202004e-14, -0.0, -1.7763568394002505e-15, 1.0)
BONE Head | pos=(-2.813868305029388e-10, 0.06930220127105713, 0.009001433849334717) | rot=(-2.842170943040401e-14, 6.661369911486461e-16, 2.2351740369686013e-07, 1.0)
BONE LeftShoulder | pos=(-0.019986482337117195, 0.09957098960876465, -0.028234828263521194) | rot=(1.4210854715202004e-14, -0.0, -1.7763568394002505e-15, 1.0)
BONE RightShoulder | pos=(0.019986482337117195, 0.09957098960876465, -0.028234828263521194) | rot=(-1.4210854715202004e-14, -7.660545646177158e-15, -1.8626452913395042e-07, 1.0)
BONE LeftUpperArm | pos=(-0.06144832819700241, -0.009230852127075195, -3.725290298461914e-09) | rot=(0.057419318705797195, 0.07166291773319244, 0.6341037750244141, 0.7677759528160095)
BONE RightUpperArm | pos=(0.06144832819700241, -0.009230852127075195, -3.725290298461914e-09) | rot=(0.05741172283887863, -0.071658656001091, -0.634104311466217, 0.7677764296531677)
BONE LeftLowerArm | pos=(-0.19638022780418396, 1.4901161193847656e-08, 1.0826624929904938e-08) | rot=(-0.08796463906764984, -0.0002507045865058899, 3.182684304192662e-05, 0.9961236119270325)
BONE RightLowerArm | pos=(0.19638019800186157, 1.4901161193847656e-08, 5.704350769519806e-09) | rot=(-0.08796434849500656, 0.0002495013177394867, -3.170862328261137e-05, 0.9961236119270325)
BONE LeftHand | pos=(-0.18214809894561768, 2.2532985894940794e-06, 0.0003368060861248523) | rot=(-0.0033511221408843994, -2.327142283320427e-06, 0.00025026703951880336, 0.9999943971633911)
BONE RightHand | pos=(0.18214815855026245, 2.219883754150942e-06, 0.00033680786145851016) | rot=(-0.003341883420944214, 2.607761189210578e-06, -0.00024992303224280477, 0.9999943375587463)
BONE LeftToes | pos=(-2.3283064365386963e-09, -0.04750719666481018, 0.07360689342021942) | rot=(5.820766091346741e-11, -3.725290298461914e-09, 1.4551915228366852e-10, 1.0)
BONE RightToes | pos=(-2.7939677238464355e-09, -0.047507043927907944, 0.07360687851905823) | rot=(-0.0, 3.7252898543727042e-09, -2.2737364768765644e-10, 1.0)
BONE LeftThumbProximal | pos=(-0.0028789639472961426, -0.008631706237792969, 0.013767581433057785) | rot=(0.004594407044351101, -0.012576517648994923, 0.10340922325849533, 0.9945487976074219)
BONE LeftThumbIntermediate | pos=(-0.028898894786834717, -0.0017788410186767578, 0.026099927723407745) | rot=(-0.004333410412073135, -0.05634036660194397, 0.0023273671977221966, 0.9983994960784912)
BONE LeftThumbDistal | pos=(-0.018433421850204468, -0.0008366107940673828, 0.01522209495306015) | rot=(-0.00023532845079898834, -0.18862757086753845, 0.0001263730227947235, 0.982048749923706)
BONE LeftIndexProximal | pos=(-0.053496330976486206, 0.006365060806274414, 0.01695294678211212) | rot=(0.013601275160908699, 0.013662959448993206, 0.19651445746421814, 0.980311393737793)
BONE LeftIndexIntermediate | pos=(-0.027173638343811035, 0.0, -0.0005573257803916931) | rot=(0.007570882327854633, 0.0027571548707783222, 0.11912958323955536, 0.9928460121154785)
BONE LeftIndexDistal | pos=(-0.016714155673980713, -0.0005099773406982422, -0.0006804503500461578) | rot=(0.005011396016925573, 0.0018250425346195698, 0.18620502948760986, 0.982496440410614)
BONE LeftMiddleProximal | pos=(-0.05422517657279968, 0.006365180015563965, 0.0017654336988925934) | rot=(0.004319216590374708, 0.005225885193794966, 0.14717106521129608, 0.9890878200531006)
BONE LeftMiddleIntermediate | pos=(-0.030273795127868652, -1.1920928955078125e-07, 0.0) | rot=(0.0, 0.0, 0.1006290465593338, 0.994924008846283)
BONE LeftMiddleDistal | pos=(-0.018676578998565674, 1.1920928955078125e-07, 0.0) | rot=(0.0, 0.0, 0.18108375370502472, 0.9834677577018738)
BONE LeftRingProximal | pos=(-0.05360749363899231, 0.006365060806274414, -0.01171904057264328) | rot=(-0.007630121428519487, 0.0006041940650902689, 0.18622227013111115, 0.9824779033660889)
BONE LeftRingIntermediate | pos=(-0.02808278799057007, 0.0, -7.450580596923828e-09) | rot=(8.752217439678134e-08, 2.8882318758860492e-08, 0.11663421988487244, 0.9931749701499939)
BONE LeftRingDistal | pos=(-0.016206741333007812, 0.0, -7.450580596923828e-09) | rot=(3.715712537655236e-08, 1.2261837767368888e-08, 0.23141497373580933, 0.9728551506996155)
BONE LeftLittleProximal | pos=(-0.04991629719734192, 0.006365060806274414, -0.025162536650896072) | rot=(-0.015178955160081387, 0.000255459948675707, 0.18878304958343506, 0.9819015264511108)
BONE LeftLittleIntermediate | pos=(-0.02568751573562622, 1.1920928955078125e-07, 1.4901161193847656e-08) | rot=(-1.698765998980889e-07, -5.60593420573241e-08, 0.13961189985275269, 0.9902063608169556)
BONE LeftLittleDistal | pos=(-0.014804661273956299, 0.0, 1.4901161193847656e-08) | rot=(-1.5471083258944418e-07, -5.105461653442944e-08, 0.15553614497184753, 0.9878302216529846)
BONE RightThumbProximal | pos=(0.0028789639472961426, -0.008631706237792969, 0.013767581433057785) | rot=(0.004594414494931698, 0.012576516717672348, -0.10340920090675354, 0.9945487976074219)
BONE RightThumbIntermediate | pos=(0.028898894786834717, -0.0017788410186767578, 0.026099927723407745) | rot=(-0.004333405755460262, 0.05634036660194397, -0.0023273671977221966, 0.9983994960784912)
BONE RightThumbDistal | pos=(0.018433421850204468, -0.0008366107940673828, 0.01522209495306015) | rot=(-0.00023522970150224864, 0.18862754106521606, -0.0001263320300495252, 0.9820486903190613)
BONE RightIndexProximal | pos=(0.053496330976486206, 0.006365060806274414, 0.01695294678211212) | rot=(0.013601006008684635, -0.01366385817527771, -0.19651442766189575, 0.980311393737793)
BONE RightIndexIntermediate | pos=(0.027173638343811035, 0.0, -0.0005573257803916931) | rot=(0.007570882327854633, -0.0027571541722863913, -0.11912958323955536, 0.9928460121154785)
BONE RightIndexDistal | pos=(0.016714155673980713, -0.0005099773406982422, -0.0006804503500461578) | rot=(0.005011396016925573, -0.0018250418361276388, -0.18620502948760986, 0.982496440410614)
BONE RightMiddleProximal | pos=(0.05422517657279968, 0.006365180015563965, 0.0017654336988925934) | rot=(0.004319108556956053, -0.005226242821663618, -0.1471710354089737, 0.9890878200531006)
BONE RightMiddleIntermediate | pos=(0.030273795127868652, -1.1920928955078125e-07, 0.0) | rot=(0.0, 0.0, -0.1006290465593338, 0.994924008846283)
BONE RightMiddleDistal | pos=(0.018676578998565674, 1.1920928955078125e-07, 0.0) | rot=(0.0, 0.0, -0.18108375370502472, 0.9834677577018738)
BONE RightRingProximal | pos=(0.05360749363899231, 0.006365060806274414, -0.01171904057264328) | rot=(-0.007630336098372936, -0.0006049163057468832, -0.18622227013111115, 0.9824778437614441)
BONE RightRingIntermediate | pos=(0.02808278799057007, 0.0, -7.450580596923828e-09) | rot=(8.752217439678134e-08, -2.8882308100719456e-08, -0.11663421988487244, 0.9931749701499939)
BONE RightRingDistal | pos=(0.016206741333007812, 0.0, -7.450580596923828e-09) | rot=(3.715712537655236e-08, -1.226183421465521e-08, -0.23141497373580933, 0.9728551506996155)
BONE RightLittleProximal | pos=(0.04991629719734192, 0.006365060806274414, -0.025162536650896072) | rot=(-0.01517949253320694, -0.0002572536759544164, -0.18878304958343506, 0.9819015264511108)
BONE RightLittleIntermediate | pos=(0.02568751573562622, 1.1920928955078125e-07, 1.4901161193847656e-08) | rot=(-1.698765998980889e-07, 5.60593420573241e-08, -0.13961189985275269, 0.9902063608169556)
BONE RightLittleDistal | pos=(0.014804661273956299, 0.0, 1.4901161193847656e-08) | rot=(-1.547108468002989e-07, 5.105461653442944e-08, -0.15553614497184753, 0.9878302216529846)
BONE UpperChest | pos=(2.7935120130667223e-17, 0.09951698780059814, -0.013566743582487106) | rot=(1.9371510973087425e-07, -0.0, -1.4901162970204496e-08, 1.0)
/VMC/Ext/Tra/Pos ('Head', -0.0017851670272648335, 1.3348503112792969, -0.01629537157714367, -1.3038555835009902e-07, -5.084881138373021e-08, -1.817620471911141e-07, -1.0)
/VMC/Ext/Tra/Pos ('Spine', -0.0017851896118372679, 0.9428538680076599, 0.022743944078683853, 6.332956559162994e-08, -5.0848846910866996e-08, 2.685418643011417e-08, -1.0)
/VMC/Ext/Tra/Pos ('Pelvis', -0.0017851905431598425, 0.8962059617042542, 0.011255824938416481, 2.719457938837877e-07, 6.836042132363218e-08, 2.6854168666545775e-08, -1.0)
/VMC/Ext/Tra/Pos ('LeftHand', -0.15345346927642822, 0.8595749139785767, -0.0030604256317019463, 0.012753472663462162, -0.013274846598505974, -0.6382002830505371, -0.7696503400802612)
/VMC/Ext/Tra/Pos ('RightHand', 0.14988282322883606, 0.8595749139785767, -0.0030588479712605476, 0.012754406780004501, 0.013277262449264526, 0.6382001042366028, -0.7696502804756165)
/VMC/Ext/Tra/Pos ('LeftFoot', -0.05560366064310074, 0.08836579322814941, -0.020324869081377983, 0.004773406777530909, 0.00255265599116683, -0.01362998690456152, -0.9998924732208252)
/VMC/Ext/Tra/Pos ('RightFoot', 0.05203244462609291, 0.08836573362350464, -0.020325077697634697, 0.004773410968482494, -0.0025519307237118483, 0.013630923815071583, -0.99989253282547)
'''


def _parse_floats(text: str) -> Tuple[float, ...]:
    return tuple(float(part.strip()) for part in text.split(",") if part.strip())


def _parse_standing_capture() -> Tuple[Tuple[str, Vec3, Quat], Dict[str, Tuple[Vec3, Quat]], Dict[str, Tuple[Vec3, Quat]]]:
    root: Tuple[str, Vec3, Quat] = ("root", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))
    bones: Dict[str, Tuple[Vec3, Quat]] = {}
    trackers: Dict[str, Tuple[Vec3, Quat]] = {}

    bone_re = re.compile(r"^BONE\s+(\S+)\s+\|\s+pos=\(([^)]*)\)\s+\|\s+rot=\(([^)]*)\)")
    root_re = re.compile(r"^ROOT\s+(\S+)\s+\|\s+pos=\(([^)]*)\)\s+\|\s+rot=\(([^)]*)\)")
    tracker_re = re.compile(r"^/VMC/Ext/Tra/Pos\s+\('([^']+)',\s*(.*)\)")

    for line in STANDING_CAPTURE.splitlines():
        line = line.strip()
        if not line:
            continue
        m = root_re.match(line)
        if m:
            pos = _parse_floats(m.group(2))
            rot = _parse_floats(m.group(3))
            root = (m.group(1), pos[:3], rot[:4])  # type: ignore[assignment]
            continue
        m = bone_re.match(line)
        if m:
            name = m.group(1)
            if name in SKIP_BONES:
                continue
            pos = _parse_floats(m.group(2))
            rot = _parse_floats(m.group(3))
            bones[name] = (pos[:3], rot[:4])  # type: ignore[assignment]
            continue
        m = tracker_re.match(line)
        if m:
            name = m.group(1)
            nums = _parse_floats(m.group(2))
            if len(nums) >= 7:
                trackers[name] = (nums[:3], nums[3:7])  # type: ignore[assignment]

    return root, bones, trackers


ROOT_POSE, STANDING_BONES, STANDING_TRACKERS = _parse_standing_capture()


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _scaled_shape(shape: Dict[str, float]) -> Dict[str, float]:
    """V5.0/V5.1 mouth shaping: small secondary vowel blends are allowed."""
    out: Dict[str, float] = {}
    for name in VOWEL_BLENDS:
        value = shape.get(name, 0.0)
        if value > 0.0:
            value += random.uniform(-RANDOM_AMOUNT, RANDOM_AMOUNT)
        out[name] = _clamp01(value * MOUTH_SCALE)
    return out


def _scale_expression(shape: Dict[str, float], strength: float) -> Dict[str, float]:
    return {name: _clamp01(shape.get(name, 0.0) * strength) for name in EXPRESSION_BLENDS}


def _euler_deg_to_quat(x_deg: float = 0.0, y_deg: float = 0.0, z_deg: float = 0.0) -> Quat:
    x = math.radians(x_deg) * 0.5
    y = math.radians(y_deg) * 0.5
    z = math.radians(z_deg) * 0.5
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    qx = sx * cy * cz + cx * sy * sz
    qy = cx * sy * cz - sx * cy * sz
    qz = cx * cy * sz + sx * sy * cz
    qw = cx * cy * cz - sx * sy * sz
    return _quat_normalize((qx, qy, qz, qw))


def _quat_multiply(a: Quat, b: Quat) -> Quat:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return _quat_normalize((
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ))


def _quat_normalize(q: Quat) -> Quat:
    x, y, z, w = q
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length <= 0.0:
        return (0.0, 0.0, 0.0, 1.0)
    return (x / length, y / length, z / length, w / length)


def _rough_text_to_visemes(text: str) -> List[VisemeEvent]:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9 .,!?;:'\-]", " ", text)

    events: List[VisemeEvent] = []
    last_was_vowel = False

    for ch in text:
        if ch in "aeiou":
            events.append(VisemeEvent(_scaled_shape(VISEME_SHAPES[ch]), random.uniform(0.055, 0.095)))
            last_was_vowel = True
        elif ch in "mnmbpfv":
            events.append(VisemeEvent(_scaled_shape(VISEME_SHAPES["closed"]), random.uniform(0.030, 0.055)))
            last_was_vowel = False
        elif ch in "bcdfghjklpqrstvwxyz":
            if last_was_vowel and random.random() < 0.65:
                events.append(VisemeEvent(_scaled_shape(VISEME_SHAPES["closed"]), random.uniform(0.020, 0.045)))
            elif random.random() < 0.35:
                events.append(VisemeEvent(_scaled_shape(VISEME_SHAPES["small"]), random.uniform(0.025, 0.050)))
            last_was_vowel = False
        elif ch == " ":
            if events and random.random() < 0.45:
                events.append(VisemeEvent(_scaled_shape(VISEME_SHAPES["closed"]), random.uniform(0.035, 0.075)))
            last_was_vowel = False
        elif ch in ",;:":
            events.append(VisemeEvent(_scaled_shape(VISEME_SHAPES["closed"]), random.uniform(0.075, 0.130)))
            last_was_vowel = False
        elif ch in ".!?:":
            events.append(VisemeEvent(_scaled_shape(VISEME_SHAPES["closed"]), random.uniform(0.120, 0.220)))
            last_was_vowel = False

    if not events:
        for _ in range(20):
            vowel = random.choice("aeiou")
            events.append(VisemeEvent(_scaled_shape(VISEME_SHAPES[vowel]), random.uniform(0.055, 0.095)))
            events.append(VisemeEvent(_scaled_shape(VISEME_SHAPES["closed"]), random.uniform(0.025, 0.055)))

    return events


# expression_keyword_scores(), expression_from_text(), and best_expression_score()
# are imported from avatar/expression_keywords.py.


class VMCAvatarController:
    def __init__(self, ip: str = VSEEFACE_IP, port: int = VSEEFACE_PORT):
        self.ip = ip
        self.port = port
        self.client = None
        self._enabled = SimpleUDPClient is not None

        self._send_lock = threading.Lock()
        self._state_lock = threading.RLock()

        self._blend_state: Dict[str, float] = {name: 0.0 for name in VOWEL_BLENDS + EXPRESSION_BLENDS}
        self._mouth_state: Dict[str, float] = {name: 0.0 for name in VOWEL_BLENDS}
        self._last_blend_sent: Dict[str, float] = {name: -999.0 for name in VOWEL_BLENDS + EXPRESSION_BLENDS}
        self._expression_current: Dict[str, float] = _scale_expression(EXPRESSION_PRESETS["soft"], IDLE_EXPRESSION_STRENGTH)
        self._expression_target: Dict[str, float] = dict(self._expression_current)
        self._expression_hold_until = 0.0

        self._body_expression_current: Dict[str, float] = _scale_body_expression(BODY_EXPRESSION_PRESETS["soft"], BODY_EXPRESSION_STRENGTH)
        self._body_expression_target: Dict[str, float] = dict(self._body_expression_current)
        self._body_expression_hold_until = 0.0
        self._body_expression_started_at = 0.0
        self._last_expression_preset = "soft"

        self._mouth_thread: Optional[threading.Thread] = None
        self._mouth_stop_event = threading.Event()

        self._idle_thread: Optional[threading.Thread] = None
        self._idle_stop_event = threading.Event()
        self._speaking = False

        if self._enabled:
            self.client = SimpleUDPClient(self.ip, self.port)
        else:
            print("⚠️ python-osc is not installed. VMC avatar control is disabled.")
            print("   Install it with: pip install python-osc")

        if AUTO_START_IDLE:
            self.start_idle()

    # ------------------------------------------------------------------
    # Low-level VMC send helpers
    # ------------------------------------------------------------------

    def _apply_all_blends(self) -> None:
        """Apply mouth + expressions together in one VMC blend frame.

        V5.5: this is the only method that should call /VMC/Ext/Blend/Apply
        during normal operation. Mouth code updates self._mouth_state, expression
        code updates self._blend_state, and this method sends both together.
        """
        if not self._enabled or self.client is None:
            return

        with self._send_lock:
            with self._state_lock:
                values = {}
                for name in VOWEL_BLENDS:
                    values[name] = _clamp01(self._mouth_state.get(name, 0.0))
                for name in EXPRESSION_BLENDS:
                    values[name] = _clamp01(self._blend_state.get(name, 0.0))

            # Always send all vowel blends together. This prevents stale mouth
            # shapes inside VSeeFace when switching A/I/U/E/O.
            for name in VOWEL_BLENDS:
                self.client.send_message("/VMC/Ext/Blend/Val", [name, values.get(name, 0.0)])

            # Send expressions in the same apply packet, so they do not interrupt
            # the mouth with their own separate /Blend/Apply call.
            for name in EXPRESSION_BLENDS:
                self.client.send_message("/VMC/Ext/Blend/Val", [name, values.get(name, 0.0)])

            self.client.send_message("/VMC/Ext/Blend/Apply", [])
            self._last_blend_sent = values

    def _send_root(self, pos: Vec3, quat: Quat) -> None:
        if not self._enabled or self.client is None:
            return
        name = ROOT_POSE[0]
        self.client.send_message("/VMC/Ext/Root/Pos", [name, pos[0], pos[1], pos[2], quat[0], quat[1], quat[2], quat[3]])

    def _send_bone(self, bone_name: str, pos: Vec3, quat: Quat) -> None:
        if not self._enabled or self.client is None:
            return
        self.client.send_message("/VMC/Ext/Bone/Pos", [bone_name, pos[0], pos[1], pos[2], quat[0], quat[1], quat[2], quat[3]])

    def _send_tracker(self, tracker_name: str, pos: Vec3, quat: Quat) -> None:
        if not self._enabled or self.client is None:
            return
        self.client.send_message("/VMC/Ext/Tra/Pos", [tracker_name, pos[0], pos[1], pos[2], quat[0], quat[1], quat[2], quat[3]])

    def _send_ok_time(self) -> None:
        if not self._enabled or self.client is None:
            return
        self.client.send_message("/VMC/Ext/OK", [1])
        self.client.send_message("/VMC/Ext/T", [float(time.perf_counter())])

    def _standing_pose_with_idle(self, now: float) -> Tuple[Vec3, Quat, Dict[str, Tuple[Vec3, Quat]], Dict[str, Tuple[Vec3, Quat]]]:
        root_name, root_pos, root_rot = ROOT_POSE
        _ = root_name

        with self._state_lock:
            body_expr = dict(self._body_expression_current)
            speaking = self._speaking
            body_expression_active = now < self._body_expression_hold_until

        # V6.2: expression poses should read clearly, but hard-freezing idle caused
        # rhythmic pauses. Only reduce idle while Akira is speaking a real pose.
        suppress_idle = DISABLE_IDLE_DURING_EXPRESSIONS and speaking and body_expr.get("energy", 0.0) != 0.0
        base_strength = max(0.0, BODY_IDLE_STRENGTH)
        if suppress_idle:
            base_strength *= max(0.0, min(1.0, IDLE_STRENGTH_DURING_EXPRESSIONS))

        energy = body_expr.get("energy", 0.0)
        motion_boost = 1.0 + max(-0.45, min(1.25, energy)) * 0.55
        if speaking and not suppress_idle:
            motion_boost += BODY_SPEAKING_MOTION_BOOST

        strength = base_strength * max(0.30, motion_boost)

        # Slow layered motion. The different speeds prevent the idle from looking
        # like a simple metronome while still staying predictable and calm.
        breath = math.sin(now * 1.18) * strength
        sway = math.sin(now * 0.55 + 1.0) * strength
        drift = math.sin(now * 0.31 + 2.3) * strength
        arm = math.sin(now * 0.82 + 0.4) * strength

        # Small speech pulse makes her body feel like it is reacting to her own
        # words without trying to match every syllable.
        talk_wave = 0.0
        if speaking and not suppress_idle:
            talk_wave = math.sin(now * 5.2) * (0.45 + max(0.0, energy) * 0.35)

        # Root movement makes the whole body visibly breathe/sway instead of only
        # rotating the head. Kept small so feet do not look like they are sliding.
        root_pos2: Vec3 = (
            root_pos[0]
            + body_expr.get("root_x_m", 0.0)
            + BODY_SWAY_METERS * 0.35 * sway
            + body_expr.get("root_sway_m", 0.0) * math.sin(now * 0.70 + 0.5),
            root_pos[1]
            + body_expr.get("root_y_m", 0.0)
            + BODY_ROOT_BOB_METERS * breath
            + body_expr.get("root_bounce_m", 0.0) * (0.45 + 0.55 * abs(math.sin(now * 1.50))),
            root_pos[2]
            + body_expr.get("root_z_m", 0.0)
            + BODY_SWAY_METERS * 0.20 * drift
            + body_expr.get("root_forward_m", 0.0),
        )

        bones: Dict[str, Tuple[Vec3, Quat]] = {}
        for name, (pos, quat) in STANDING_BONES.items():
            q = quat
            p = pos

            if strength > 0.0:
                if name == "Spine":
                    q = _quat_multiply(
                        quat,
                        _euler_deg_to_quat(
                            0.40 * BODY_BREATH_DEGREES * breath,
                            0.20 * sway,
                            0.25 * drift,
                        ),
                    )
                elif name == "Chest":
                    q = _quat_multiply(
                        quat,
                        _euler_deg_to_quat(
                            0.65 * BODY_BREATH_DEGREES * breath
                            + BODY_TALK_PULSE_DEGREES * 0.25 * talk_wave,
                            0.28 * sway,
                            0.35 * drift,
                        ),
                    )
                elif name == "UpperChest":
                    q = _quat_multiply(
                        quat,
                        _euler_deg_to_quat(
                            1.00 * BODY_BREATH_DEGREES * breath
                            + BODY_TALK_PULSE_DEGREES * 0.35 * talk_wave,
                            0.35 * sway,
                            0.50 * drift,
                        ),
                    )
                elif name == "Neck":
                    q = _quat_multiply(
                        quat,
                        _euler_deg_to_quat(
                            0.80 * breath,
                            0.55 * BODY_HEAD_YAW_DEGREES * sway,
                            0.35 * drift,
                        ),
                    )
                elif name == "Head":
                    q = _quat_multiply(
                        quat,
                        _euler_deg_to_quat(
                            1.10 * breath
                            + BODY_TALK_PULSE_DEGREES * 0.18 * talk_wave,
                            BODY_HEAD_YAW_DEGREES * sway,
                            0.65 * drift,
                        ),
                    )
                elif name == "LeftShoulder":
                    q = _quat_multiply(
                        quat,
                        _euler_deg_to_quat(
                            0.25 * arm + body_expr.get("shoulder_pitch_deg", 0.0),
                            0.0,
                            0.35 * drift + body_expr.get("shoulder_roll_deg", 0.0),
                        ),
                    )
                elif name == "RightShoulder":
                    q = _quat_multiply(
                        quat,
                        _euler_deg_to_quat(
                            0.25 * arm + body_expr.get("shoulder_pitch_deg", 0.0),
                            0.0,
                            -0.35 * drift - body_expr.get("shoulder_roll_deg", 0.0),
                        ),
                    )
                elif name == "LeftUpperArm":
                    q = _quat_multiply(
                        quat,
                        _euler_deg_to_quat(
                            0.45 * arm
                            + body_expr.get("arm_pitch_deg", 0.0)
                            + BODY_TALK_PULSE_DEGREES * 0.30 * talk_wave,
                            0.15 * sway,
                            BODY_ARM_SWAY_DEGREES * 0.55 * drift + body_expr.get("arm_roll_deg", 0.0),
                        ),
                    )
                elif name == "RightUpperArm":
                    q = _quat_multiply(
                        quat,
                        _euler_deg_to_quat(
                            0.45 * arm
                            + body_expr.get("arm_pitch_deg", 0.0)
                            + BODY_TALK_PULSE_DEGREES * 0.30 * talk_wave,
                            -0.15 * sway,
                            -BODY_ARM_SWAY_DEGREES * 0.55 * drift - body_expr.get("arm_roll_deg", 0.0),
                        ),
                    )
                elif name == "LeftLowerArm":
                    q = _quat_multiply(
                        quat,
                        _euler_deg_to_quat(
                            0.35 * arm + 0.55 * body_expr.get("arm_pitch_deg", 0.0),
                            0.0,
                            BODY_ARM_SWAY_DEGREES * 0.35 * sway + 0.45 * body_expr.get("arm_roll_deg", 0.0),
                        ),
                    )
                elif name == "RightLowerArm":
                    q = _quat_multiply(
                        quat,
                        _euler_deg_to_quat(
                            0.35 * arm + 0.55 * body_expr.get("arm_pitch_deg", 0.0),
                            0.0,
                            -BODY_ARM_SWAY_DEGREES * 0.35 * sway - 0.45 * body_expr.get("arm_roll_deg", 0.0),
                        ),
                    )
                elif name == "LeftHand":
                    q = _quat_multiply(
                        quat,
                        _euler_deg_to_quat(
                            0.25 * arm + 0.35 * body_expr.get("arm_pitch_deg", 0.0),
                            0.0,
                            BODY_ARM_SWAY_DEGREES * 0.25 * drift + 0.25 * body_expr.get("arm_roll_deg", 0.0),
                        ),
                    )
                elif name == "RightHand":
                    q = _quat_multiply(
                        quat,
                        _euler_deg_to_quat(
                            0.25 * arm + 0.35 * body_expr.get("arm_pitch_deg", 0.0),
                            0.0,
                            -BODY_ARM_SWAY_DEGREES * 0.25 * drift - 0.25 * body_expr.get("arm_roll_deg", 0.0),
                        ),
                    )

            # V5.8 actual pose offsets. These are larger, mostly static pose targets
            # layered on top of the normal idle motion, so emotions read as poses
            # instead of simply changing how wavy the idle loop is.
            if ENABLE_BODY_EXPRESSIONS:
                if name == "Spine":
                    q = _quat_multiply(q, _euler_deg_to_quat(
                        body_expr.get("spine_pitch_deg", 0.0),
                        body_expr.get("spine_yaw_deg", 0.0),
                        body_expr.get("spine_roll_deg", 0.0),
                    ))
                elif name == "Chest":
                    q = _quat_multiply(q, _euler_deg_to_quat(
                        body_expr.get("chest_pitch_deg", 0.0),
                        body_expr.get("chest_yaw_deg", 0.0),
                        body_expr.get("chest_roll_deg", 0.0),
                    ))
                elif name == "UpperChest":
                    q = _quat_multiply(q, _euler_deg_to_quat(
                        body_expr.get("upper_chest_pitch_deg", 0.0),
                        body_expr.get("upper_chest_yaw_deg", 0.0),
                        body_expr.get("upper_chest_roll_deg", 0.0),
                    ))
                elif name == "Neck":
                    q = _quat_multiply(q, _euler_deg_to_quat(
                        body_expr.get("neck_pitch_deg", 0.0),
                        body_expr.get("neck_yaw_deg", 0.0),
                        body_expr.get("neck_roll_deg", 0.0),
                    ))
                elif name == "Head":
                    q = _quat_multiply(q, _euler_deg_to_quat(
                        body_expr.get("head_pitch_deg", 0.0),
                        body_expr.get("head_yaw_deg", 0.0),
                        body_expr.get("head_roll_deg", 0.0),
                    ))
                elif name == "LeftShoulder":
                    q = _quat_multiply(q, _euler_deg_to_quat(
                        body_expr.get("left_shoulder_pitch_deg", 0.0),
                        body_expr.get("left_shoulder_yaw_deg", 0.0),
                        body_expr.get("left_shoulder_roll_deg", 0.0),
                    ))
                elif name == "RightShoulder":
                    q = _quat_multiply(q, _euler_deg_to_quat(
                        body_expr.get("right_shoulder_pitch_deg", 0.0),
                        body_expr.get("right_shoulder_yaw_deg", 0.0),
                        body_expr.get("right_shoulder_roll_deg", 0.0),
                    ))
                elif name == "LeftUpperArm":
                    q = _quat_multiply(q, _euler_deg_to_quat(
                        body_expr.get("left_upperarm_pitch_deg", 0.0),
                        body_expr.get("left_upperarm_yaw_deg", 0.0),
                        body_expr.get("left_upperarm_roll_deg", 0.0),
                    ))
                elif name == "RightUpperArm":
                    q = _quat_multiply(q, _euler_deg_to_quat(
                        body_expr.get("right_upperarm_pitch_deg", 0.0),
                        body_expr.get("right_upperarm_yaw_deg", 0.0),
                        body_expr.get("right_upperarm_roll_deg", 0.0),
                    ))
                elif name == "LeftLowerArm":
                    q = _quat_multiply(q, _euler_deg_to_quat(
                        body_expr.get("left_lowerarm_pitch_deg", 0.0),
                        body_expr.get("left_lowerarm_yaw_deg", 0.0),
                        body_expr.get("left_lowerarm_roll_deg", 0.0),
                    ))
                elif name == "RightLowerArm":
                    q = _quat_multiply(q, _euler_deg_to_quat(
                        body_expr.get("right_lowerarm_pitch_deg", 0.0),
                        body_expr.get("right_lowerarm_yaw_deg", 0.0),
                        body_expr.get("right_lowerarm_roll_deg", 0.0),
                    ))
                elif name == "LeftHand":
                    p = (
                        p[0] + body_expr.get("left_hand_x_m", 0.0),
                        p[1] + body_expr.get("left_hand_y_m", 0.0),
                        p[2] + body_expr.get("left_hand_z_m", 0.0),
                    )
                    q = _quat_multiply(q, _euler_deg_to_quat(
                        body_expr.get("left_hand_pitch_deg", 0.0),
                        body_expr.get("left_hand_yaw_deg", 0.0),
                        body_expr.get("left_hand_roll_deg", 0.0),
                    ))
                elif name == "RightHand":
                    p = (
                        p[0] + body_expr.get("right_hand_x_m", 0.0),
                        p[1] + body_expr.get("right_hand_y_m", 0.0),
                        p[2] + body_expr.get("right_hand_z_m", 0.0),
                    )
                    q = _quat_multiply(q, _euler_deg_to_quat(
                        body_expr.get("right_hand_pitch_deg", 0.0),
                        body_expr.get("right_hand_yaw_deg", 0.0),
                        body_expr.get("right_hand_roll_deg", 0.0),
                    ))

            bones[name] = (p, q)

        trackers: Dict[str, Tuple[Vec3, Quat]] = {}
        for name, (pos, quat) in STANDING_TRACKERS.items():
            p = pos
            q = quat
            if strength > 0.0:
                if name == "Head":
                    p = (
                        pos[0] + body_expr.get("root_x_m", 0.0) + BODY_SWAY_METERS * 0.75 * sway,
                        pos[1] + body_expr.get("root_y_m", 0.0) + BODY_ROOT_BOB_METERS * breath + body_expr.get("root_bounce_m", 0.0) * 0.60,
                        pos[2] + body_expr.get("root_z_m", 0.0) + BODY_SWAY_METERS * 0.30 * drift + body_expr.get("root_forward_m", 0.0) * 0.35,
                    )
                elif name == "Spine":
                    p = (
                        pos[0] + BODY_SWAY_METERS * 0.45 * sway,
                        pos[1] + BODY_ROOT_BOB_METERS * 0.70 * breath,
                        pos[2] + BODY_SWAY_METERS * 0.20 * drift + body_expr.get("root_forward_m", 0.0) * 0.25,
                    )
                elif name == "Pelvis":
                    p = (
                        pos[0] + BODY_SWAY_METERS * 0.25 * sway,
                        pos[1] + BODY_ROOT_BOB_METERS * 0.45 * breath,
                        pos[2],
                    )
                elif name == "LeftHand":
                    # V5.8: hand trackers get larger offsets because VSeeFace may
                    # use them more visibly than raw upper-arm rotations.
                    p = (
                        pos[0]
                        + BODY_SWAY_METERS * 0.20 * sway
                        + body_expr.get("left_hand_x_m", 0.0)
                        + ARM_SPEAKING_SWAY_METERS * 0.35 * talk_wave,
                        pos[1]
                        + BODY_ROOT_BOB_METERS * 0.65 * breath
                        + body_expr.get("hand_y_m", 0.0)
                        + body_expr.get("left_hand_y_m", 0.0)
                        + BODY_TALK_HAND_METERS * 0.40 * talk_wave
                        + ARM_SPEAKING_LIFT_METERS * abs(talk_wave),
                        pos[2]
                        + BODY_SWAY_METERS * 0.50 * drift
                        + body_expr.get("hand_z_m", 0.0)
                        + body_expr.get("left_hand_z_m", 0.0)
                        - ARM_SPEAKING_SWAY_METERS * 0.45 * abs(talk_wave),
                    )
                elif name == "RightHand":
                    # V5.8: hand trackers get larger offsets because VSeeFace may
                    # use them more visibly than raw upper-arm rotations.
                    p = (
                        pos[0]
                        + BODY_SWAY_METERS * 0.20 * sway
                        + body_expr.get("right_hand_x_m", 0.0)
                        - ARM_SPEAKING_SWAY_METERS * 0.35 * talk_wave,
                        pos[1]
                        + BODY_ROOT_BOB_METERS * 0.65 * breath
                        + body_expr.get("hand_y_m", 0.0)
                        + body_expr.get("right_hand_y_m", 0.0)
                        + BODY_TALK_HAND_METERS * 0.40 * talk_wave
                        + ARM_SPEAKING_LIFT_METERS * abs(talk_wave),
                        pos[2]
                        + BODY_SWAY_METERS * 0.50 * drift
                        + body_expr.get("hand_z_m", 0.0)
                        + body_expr.get("right_hand_z_m", 0.0)
                        - ARM_SPEAKING_SWAY_METERS * 0.45 * abs(talk_wave),
                    )
            trackers[name] = (p, q)

        return root_pos2, root_rot, bones, trackers

    def send_standing_pose_once(self) -> None:
        if not ENABLE_STANDING_POSE_REPLAY or not self._enabled or self.client is None:
            return

        now = time.time()
        root_pos, root_rot, bones, trackers = self._standing_pose_with_idle(now)

        with self._send_lock:
            self._send_root(root_pos, root_rot)
            for bone_name, (pos, quat) in bones.items():
                self._send_bone(bone_name, pos, quat)
            if SEND_TRACKER_POSITIONS:
                for tracker_name, (pos, quat) in trackers.items():
                    self._send_tracker(tracker_name, pos, quat)
            self._send_ok_time()

    # ------------------------------------------------------------------
    # Mouth controls
    # ------------------------------------------------------------------

    def _set_mouth_blends_no_apply(self, blends: Dict[str, float]) -> None:
        """Update mouth state only; the face loop applies it with expressions."""
        with self._state_lock:
            for name in VOWEL_BLENDS:
                self._mouth_state[name] = _clamp01(blends.get(name, 0.0))

    def set_mouth_blends(self, blends: Dict[str, float]) -> None:
        # V5.5: do not call /Blend/Apply here. The coordinated face loop applies
        # mouth + expression together at a steady rate.
        self._set_mouth_blends_no_apply(blends)

    def close_mouth(self) -> None:
        self._set_mouth_blends_no_apply({name: 0.0 for name in VOWEL_BLENDS})
        self._apply_all_blends()

    def start_talking(self, text: str = "") -> None:
        if not self._enabled:
            return
        with self._state_lock:
            if self._mouth_thread is not None and self._mouth_thread.is_alive():
                return
            self._speaking = True
            self._mouth_stop_event.clear()
            if ENABLE_EXPRESSIONS or ENABLE_BODY_EXPRESSIONS:
                self.set_expression_from_text(text, apply=True)
            self._mouth_thread = threading.Thread(target=self._mouth_loop, args=(text,), daemon=True)
            self._mouth_thread.start()

    def stop_talking(self) -> None:
        if not self._enabled:
            return
        with self._state_lock:
            thread = self._mouth_thread
            if thread is None:
                self.close_mouth()
                self._speaking = False
                self.set_expression("soft", strength=IDLE_EXPRESSION_STRENGTH)
                return
            self._mouth_stop_event.set()

        thread.join(timeout=2.0)
        self.close_mouth()

        with self._state_lock:
            self._mouth_thread = None
            self._speaking = False
        if ENABLE_EXPRESSIONS:
            self.set_expression("soft", strength=IDLE_EXPRESSION_STRENGTH)

    def _blend_toward(self, current: Dict[str, float], target: Dict[str, float], speed: float) -> Dict[str, float]:
        return {
            name: current.get(name, 0.0) + (target.get(name, 0.0) - current.get(name, 0.0)) * speed
            for name in VOWEL_BLENDS
        }

    def _mouth_loop(self, text: str) -> None:
        current = {name: 0.0 for name in VOWEL_BLENDS}
        events = _rough_text_to_visemes(text)

        for _ in range(5):
            if self._mouth_stop_event.is_set():
                self.close_mouth()
                return
            self.set_mouth_blends({name: 0.0 for name in VOWEL_BLENDS})
            time.sleep(0.015)

        if MOUTH_START_DELAY_SECONDS > 0:
            time.sleep(MOUTH_START_DELAY_SECONDS)

        frame_time = 1.0 / MOUTH_FPS
        event_index = 0

        while not self._mouth_stop_event.is_set():
            event = events[event_index]
            event_index = (event_index + 1) % len(events)
            target = event.blends
            remaining = event.duration

            while remaining > 0 and not self._mouth_stop_event.is_set():
                current_open = max(current.values())
                target_open = max(target.values())
                speed = ATTACK_SPEED if target_open >= current_open else RELEASE_SPEED
                current = self._blend_toward(current, target, speed)
                self.set_mouth_blends(current)
                sleep_time = min(frame_time, remaining)
                time.sleep(sleep_time)
                remaining -= sleep_time

        for _ in range(7):
            current = self._blend_toward(current, {name: 0.0 for name in VOWEL_BLENDS}, 0.65)
            self.set_mouth_blends(current)
            time.sleep(0.018)
        self.close_mouth()

    # ------------------------------------------------------------------
    # Expression controls
    # ------------------------------------------------------------------

    def set_expression(self, preset: str = "soft", *, strength: float = 1.0, hold_seconds: float = 2.0, apply: bool = True) -> None:
        if not ENABLE_EXPRESSIONS and not ENABLE_BODY_EXPRESSIONS:
            return

        if ENABLE_EXPRESSIONS:
            shape = EXPRESSION_PRESETS.get(preset, EXPRESSION_PRESETS["soft"])
            values = _scale_expression(shape, strength)
            with self._state_lock:
                self._last_expression_preset = preset
                self._expression_target = values
                self._expression_hold_until = time.time() + max(0.0, hold_seconds)

        if ENABLE_BODY_EXPRESSIONS:
            self.set_body_expression(preset, strength=strength, hold_seconds=hold_seconds)

        if apply:
            self._update_expression_state(force=True)
            self._update_body_expression_state(force=True)
            self._apply_all_blends()

    def set_expression_from_text(self, text: str, *, apply: bool = True) -> None:
        scores = expression_keyword_scores(text)
        preset = expression_from_text(text)
        hold = max(2.0, min(6.0, len(text) * 0.035))

        # Slightly stronger pose when there are multiple matching keywords.
        best_score = best_expression_score(text)
        strength = SPEAKING_EXPRESSION_STRENGTH
        if best_score >= 4:
            strength = min(1.0, strength * 1.12)
        elif best_score == 1:
            strength = strength * 0.88

        print(f"🎭 Expression: {preset} | scores={scores}")
        self.set_expression(preset, strength=strength, hold_seconds=hold, apply=apply)

    def clear_expressions(self) -> None:
        with self._state_lock:
            self._expression_current = {name: 0.0 for name in EXPRESSION_BLENDS}
            self._expression_target = dict(self._expression_current)
            for name in EXPRESSION_BLENDS:
                self._blend_state[name] = 0.0
        self.clear_body_expression()
        self._apply_all_blends()

    def _update_expression_state(self, *, force: bool = False) -> None:
        now = time.time()
        with self._state_lock:
            if not self._speaking and now > self._expression_hold_until:
                self._expression_target = _scale_expression(EXPRESSION_PRESETS["soft"], IDLE_EXPRESSION_STRENGTH)
            speed = 1.0 if force else EXPRESSION_FADE_SPEED
            for name in EXPRESSION_BLENDS:
                current = self._expression_current.get(name, 0.0)
                target = self._expression_target.get(name, 0.0)
                current += (target - current) * speed
                self._expression_current[name] = _clamp01(current)
                self._blend_state[name] = _clamp01(current)

    # ------------------------------------------------------------------
    # Body expression controls
    # ------------------------------------------------------------------

    def set_body_expression(self, preset: str = "soft", *, strength: float = 1.0, hold_seconds: float = 2.0) -> None:
        if not ENABLE_BODY_EXPRESSIONS:
            return
        shape = BODY_EXPRESSION_PRESETS.get(preset, BODY_EXPRESSION_PRESETS["soft"])
        values = _scale_body_expression(shape, BODY_EXPRESSION_STRENGTH * BODY_POSE_STRENGTH * strength)
        with self._state_lock:
            self._body_expression_target = values
            self._body_expression_hold_until = time.time() + max(0.0, hold_seconds)
            self._body_expression_started_at = time.time()

    def clear_body_expression(self) -> None:
        with self._state_lock:
            self._body_expression_current = _scale_body_expression(BODY_EXPRESSION_PRESETS["soft"], BODY_EXPRESSION_STRENGTH)
            self._body_expression_target = dict(self._body_expression_current)
            self._body_expression_hold_until = 0.0
            self._last_expression_preset = "soft"

    def _update_body_expression_state(self, *, force: bool = False) -> None:
        if not ENABLE_BODY_EXPRESSIONS:
            return

        now = time.time()
        with self._state_lock:
            if not self._speaking and now > self._body_expression_hold_until:
                self._body_expression_target = _scale_body_expression(BODY_EXPRESSION_PRESETS["soft"], BODY_EXPRESSION_STRENGTH)

            speed = 1.0 if force else BODY_EXPRESSION_FADE_SPEED
            for key in BODY_EXPRESSION_KEYS:
                current = self._body_expression_current.get(key, 0.0)
                target = self._body_expression_target.get(key, 0.0)
                current += (target - current) * speed
                self._body_expression_current[key] = current

    # ------------------------------------------------------------------
    # Idle loop
    # ------------------------------------------------------------------

    def start_idle(self) -> None:
        if not self._enabled or not ENABLE_IDLE_FACE:
            return
        with self._state_lock:
            if self._idle_thread is not None and self._idle_thread.is_alive():
                return
            self._idle_stop_event.clear()
            self._idle_thread = threading.Thread(target=self._idle_loop, daemon=True)
            self._idle_thread.start()

    def stop_idle(self) -> None:
        if not self._enabled:
            return
        with self._state_lock:
            thread = self._idle_thread
            if thread is None:
                return
            self._idle_stop_event.set()
        thread.join(timeout=2.0)
        with self._state_lock:
            self._idle_thread = None
        self._apply_all_blends()

    def _idle_loop(self) -> None:
        face_frame_time = 1.0 / max(1, FACE_BLEND_FPS)
        pose_frame_time = 1.0 / max(1, POSE_FPS)
        last_pose_sent = 0.0
        next_idle_expression_at = time.time() + random.uniform(5.0, 12.0)

        while not self._idle_stop_event.is_set():
            now = time.time()

            with self._state_lock:
                speaking = self._speaking

            if ENABLE_RANDOM_IDLE_EXPRESSIONS and ENABLE_EXPRESSIONS and not speaking and now >= next_idle_expression_at:
                preset = random.choice(["soft", "soft", "happy", "playful"])
                strength = IDLE_EXPRESSION_STRENGTH * random.uniform(0.70, 1.10)
                self.set_expression(preset, strength=strength, hold_seconds=random.uniform(2.0, 5.0), apply=False)
                next_idle_expression_at = now + random.uniform(5.0, 12.0)
            elif not ENABLE_RANDOM_IDLE_EXPRESSIONS:
                next_idle_expression_at = now + 999999.0

            self._update_expression_state(force=False)
            self._update_body_expression_state(force=False)

            # V5.5: one coordinated face frame applies mouth + expressions together,
            # even while speaking. The mouth thread only updates target values.
            self._apply_all_blends()

            if ENABLE_STANDING_POSE_REPLAY and now - last_pose_sent >= pose_frame_time:
                self.send_standing_pose_once()
                last_pose_sent = now

            time.sleep(face_frame_time)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        try:
            self.stop_talking()
            self.stop_idle()
            self.close_mouth()
            self.clear_expressions()
            with self._state_lock:
                for name in VOWEL_BLENDS + EXPRESSION_BLENDS:
                    self._blend_state[name] = 0.0
            self._apply_all_blends()
        except Exception:
            pass


_controller = VMCAvatarController()
atexit.register(_controller.shutdown)


def start_talking(text: str = "") -> None:
    _controller.start_talking(text)


def stop_talking() -> None:
    _controller.stop_talking()


def start_idle() -> None:
    _controller.start_idle()


def stop_idle() -> None:
    _controller.stop_idle()


def send_standing_pose_once() -> None:
    _controller.send_standing_pose_once()


def set_expression(preset: str = "soft", strength: float = 1.0) -> None:
    _controller.set_expression(preset, strength=strength)


def set_expression_from_text(text: str) -> None:
    _controller.setexpression_from_text(text)


def clear_expressions() -> None:
    _controller.clear_expressions()


def set_body_expression(preset: str = "soft", *, strength: float = 1.0, hold_seconds: float = 2.0) -> None:
    _controller.set_body_expression(preset, strength=strength, hold_seconds=hold_seconds)


def clear_body_expression() -> None:
    _controller.clear_body_expression()


def set_blends(blends: Dict[str, float]) -> None:
    with _controller._state_lock:
        for name, value in blends.items():
            if name in VOWEL_BLENDS + EXPRESSION_BLENDS:
                _controller._blend_state[name] = _clamp01(value)
    _controller._apply_all_blends()


def set_mouth(value: float) -> None:
    _controller.set_mouth_blends({"A": value})


def close_mouth() -> None:
    _controller.close_mouth()


if __name__ == "__main__":
    print(f"Testing VMC avatar control to {VSEEFACE_IP}:{VSEEFACE_PORT}")
    print("VSeeFace receiver should be ON. VSeeFace sender and capture script should be OFF.")
    print("This version replays your captured standing pose, uses V6.1 cleaned-up poses with idle suppression, and uses the coordinated V5.5 face sender.")
    print(f"Loaded {len(STANDING_BONES)} bones and {len(STANDING_TRACKERS)} trackers from the captured pose.")
    print("Press Ctrl+C to stop.")

    samples = [
        "Hey, this is Akira testing the new arm gestures, body poses, and mouth shapes.",
        "Nice, that worked perfectly. Let's go!",
        "Wait, seriously? That's actually huge!",
        "Careful, that sounds like it could break something.",
        "Bruh, your setup is chaotic, but somehow I like it.",
    ]

    try:
        start_idle()
        while True:
            for sample in samples:
                print("\nSpeaking:", sample)
                start_talking(sample)
                time.sleep(max(3.0, len(sample) * 0.055) + MOUTH_START_DELAY_SECONDS)
                stop_talking()
                time.sleep(1.5)
    except KeyboardInterrupt:
        _controller.shutdown()
        print("\nStopped.")
