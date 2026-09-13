"""Sensor abstraction layer.

Game and vision code must only ever see this interface — never a driver import.
Two real backends implement it (libfreenect for Kinect v1, pylibfreenect2 for
Kinect v2) plus a ``MockBackend`` that synthesises frames for development and
for the acceptance tests that don't need hardware.

A future tablet/AR edition replaces the detector *behind* this layer only.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Frame, SensorDescription


class SensorBackend(ABC):
    """Single interface for all depth-camera sources."""

    description: SensorDescription

    @abstractmethod
    def open(self) -> bool:
        """Connect and start streaming. Return False if the device is absent."""

    @abstractmethod
    def close(self) -> None:
        """Release the device."""

    @abstractmethod
    def grab(self) -> Frame:
        """Return the latest synchronized color + depth frame.

        ``color`` is BGR uint8; ``depth`` is millimeters float32 (or None for a
        color-only source). Must never raise for a transient miss — return the
        last good frame instead.
        """

    # -- convenience -------------------------------------------------------- #
    @property
    def has_depth(self) -> bool:
        """True when ``grab()`` yields a depth frame (Kinect). Color-only
        sources (regular webcams) override this to False so the vision pipeline
        maps the floor with a homography instead of a fitted plane."""
        return True

    @property
    def color_res(self) -> tuple[int, int]:
        return self.description.color_res

    @property
    def depth_res(self) -> tuple[int, int]:
        return self.description.depth_res

    def is_open(self) -> bool:
        return True

    def lock_capture(self, exposure: float | None = None) -> None:
        """Freeze auto exposure / white balance for play. No-op unless webcam."""

    def unlock_capture(self) -> None:
        """Restore auto exposure / white balance. No-op unless webcam."""

    def set_exposure(self, value: float) -> None:
        """Set a fixed exposure (webcam log2 seconds). No-op on other backends."""

    def capture_status(self) -> dict:
        """Lock / fps facts for the Settings camera tab."""
        return {
            "measured_fps": float(getattr(self.description, "fps", 30) or 30),
            "locked": False,
            "exposure": None,
            "ignored": [],
        }
