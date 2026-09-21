"""Observation rights and transport contracts, not benchmark performance."""
import base64
import hashlib
import io
import math
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from embodied_jev.benchmark_worker import Libero, MetaWorld, axis_angle, image_packet


@pytest.mark.parametrize("quaternion, expected", [
    ([0, 0, 0, 1], [0, 0, 0]),
    ([0, 0, 0, -1], [0, 0, 0]),
    ([0, 0, math.sqrt(.5), math.sqrt(.5)], [0, 0, math.pi / 2]),
    ([0, 0, math.sqrt(.5), -math.sqrt(.5)], [0, 0, -math.pi / 2]),
    ([2, 0, 0, 0], [math.pi, 0, 0]),
])
def test_xyzw_quaternion_uses_shortest_axis_angle(quaternion, expected):
    actual = axis_angle(quaternion)
    np.testing.assert_allclose(actual, expected, atol=1e-12)
    assert np.linalg.norm(actual) <= math.pi + 1e-12


def test_axis_angle_is_invariant_to_quaternion_sign_and_scale():
    quaternion = np.array([1., 2., 3., 4.])
    np.testing.assert_allclose(axis_angle(quaternion), axis_angle(-3 * quaternion))


@pytest.mark.parametrize("quaternion", [
    [0, 0, 0, 0], [0, 0, 1], [0, 0, 0, float("nan")],
    [float("inf"), 0, 0, 1],
])
def test_axis_angle_rejects_invalid_proprioception(quaternion):
    with pytest.raises(ValueError, match="quaternion"):
        axis_angle(quaternion)


def test_rgb_transport_preserves_camera_pixels_and_orientation():
    pixels = np.array([[[255, 0, 0], [0, 255, 0]],
                       [[0, 0, 255], [127, 128, 129]]], dtype=np.uint8)
    packet = image_packet(pixels)
    encoded = base64.b64decode(packet["data"])
    assert packet["encoding"] == "png"
    assert (packet["width"], packet["height"]) == (2, 2)
    assert packet["sha256"] == hashlib.sha256(encoded).hexdigest()
    np.testing.assert_array_equal(np.asarray(Image.open(io.BytesIO(encoded))), pixels)


def test_metaworld_vision_input_is_invariant_to_privileged_state():
    backend = object.__new__(MetaWorld)
    backend.case = {"task": "pick-place-v3"}
    backend.observation_mode = "vision"
    backend.steps = 7
    backend.raw = np.arange(39.)
    backend.env = SimpleNamespace(render=lambda: np.zeros((3, 4, 3), dtype=np.uint8))
    observation, policy_input = backend.observation(), backend.policy_input()

    # Object slots, prior privileged state and target poses must never pass
    # through either state channel when the policy receives camera observations.
    backend.raw[4:] = -9876
    assert backend.observation() == observation
    assert backend.policy_input() == policy_input
    assert set(observation) == {"source", "task", "tcp", "gripper_open_fraction"}
    assert policy_input["state"] == [0., 1., 2., 3.]
    assert set(policy_input["images"]) == {"external"}
    assert policy_input["step"] == 7


def test_libero_vision_input_keeps_only_proprioception_and_both_cameras():
    backend = object.__new__(Libero)
    backend.language = "place the bowl on the plate"
    backend.observation_mode = "vision"
    backend.steps = 2
    backend.raw = {
        "robot0_eef_pos": np.array([.1, .2, .3]),
        "robot0_eef_quat": np.array([0., 0., math.sqrt(.5), math.sqrt(.5)]),
        "robot0_gripper_qpos": np.array([.01, -.01]),
        "agentview_image": np.full((3, 4, 3), 10, dtype=np.uint8),
        "robot0_eye_in_hand_image": np.full((3, 4, 3), 20, dtype=np.uint8),
        "bowl_pos": np.array([42., 43., 44.]),
        "bowl_quat": np.array([0., 0., 0., 1.]),
        "goal_pos": np.array([87., 88., 89.]),
        "success": True,
    }
    observation, policy_input = backend.observation(), backend.policy_input()
    for key in ("bowl_pos", "bowl_quat", "goal_pos", "success"):
        backend.raw[key] = object()  # Fails if accidentally converted to floats.
    assert backend.observation() == observation
    assert backend.policy_input() == policy_input
    assert set(observation) == {"source", "task", "tcp", "gripper_qpos"}
    np.testing.assert_allclose(policy_input["state"], [.1, .2, .3, 0, 0, math.pi / 2, .01, -.01])
    assert set(policy_input["images"]) == {"external", "wrist"}
    assert policy_input["images"]["external"]["sha256"] != policy_input["images"]["wrist"]["sha256"]


def test_privileged_worker_does_not_render_images():
    backend = object.__new__(MetaWorld)
    backend.case, backend.raw = {"task": "reach-v3"}, np.arange(39.)
    backend.observation_mode, backend.steps = "privileged", 0
    # No env/render method: the existing state-only path must remain independent
    # of a display or an offscreen rendering context.
    assert backend.policy_input()["images"] == {}
    assert backend.observation()["goal"] == [36., 37., 38.]
