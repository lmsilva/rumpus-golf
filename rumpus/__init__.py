"""Rumpus Golf — camera-based floor mini golf.

A Kinect (v1 or v2) watches a living-room floor; players putt real colored
balls through DIY courses. This package owns the sensor layer, computer-vision
tracking and the game rules. The UI is a separate, replaceable web front-end
(``web/``) that is fed game state over a websocket and sends back user input.

The seam is deliberate: everything above the sensor layer is sensor-agnostic so
a future tablet/AR edition can swap only the detector (see requirements).
"""

__version__ = "0.4.0"
