import warnings
import json
from pathlib import Path

import numpy as np
import spikeinterface
from spikeinterface.core.core_tools import check_json


class Motion:
    """
    Motion of the tissue relative the probe.

    Parameters
    ----------
    displacement : numpy array 2d or list of
        Motion estimate in um.
        List is the number of segment.
        For each semgent :
            * shape (temporal bins, spatial bins)
            * motion.shape[0] = temporal_bins.shape[0]
            * motion.shape[1] = 1 (rigid) or spatial_bins.shape[1] (non rigid)
    temporal_bins_s : numpy.array 1d or list of
        temporal bins (bin center)
    spatial_bins_um : numpy.array 1d
        Windows center.
        spatial_bins_um.shape[0] == displacement.shape[1]
        If rigid then spatial_bins_um.shape[0] == 1
    direction : str, default: 'y'
        Direction of the motion.
    interpolation_method : str
        How to determine the displacement between bin centers? See the docs
        for scipy.interpolate.RegularGridInterpolator for options.
    """

    def __init__(self, displacement, temporal_bins_s, spatial_bins_um, direction="y", interpolation_method="linear"):
        if isinstance(displacement, np.ndarray):
            self.displacement = [displacement]
            assert isinstance(temporal_bins_s, np.ndarray)
            self.temporal_bins_s = [temporal_bins_s]
        else:
            assert isinstance(displacement, (list, tuple))
            self.displacement = displacement
            self.temporal_bins_s = temporal_bins_s

        assert isinstance(spatial_bins_um, np.ndarray)
        self.spatial_bins_um = spatial_bins_um

        self.num_segments = len(self.displacement)
        self.interpolators = None
        self.interpolation_method = interpolation_method

        self.direction = direction
        self.dim = ["x", "y", "z"].index(direction)
        self.check_properties()

    def check_properties(self):
        assert all(d.ndim == 2 for d in self.displacement)
        assert all(t.ndim == 1 for t in self.temporal_bins_s)
        assert all(self.spatial_bins_um.shape == (d.shape[1],) for d in self.displacement)

    def __repr__(self):
        nbins = self.spatial_bins_um.shape[0]
        if nbins == 1:
            rigid_txt = "rigid"
        else:
            rigid_txt = f"non-rigid - {nbins} spatial bins"

        interval_s = self.temporal_bins_s[0][1] - self.temporal_bins_s[0][0]
        txt = f"Motion {rigid_txt} - interval {interval_s}s - {self.num_segments} segments"
        return txt

    def make_interpolators(self):
        from scipy.interpolate import RegularGridInterpolator

        self.interpolators = [
            RegularGridInterpolator(
                (self.temporal_bins_s[j], self.spatial_bins_um), self.displacement[j], method=self.interpolation_method
            )
            for j in range(self.num_segments)
        ]
        self.temporal_bounds = [(t[0], t[-1]) for t in self.temporal_bins_s]
        self.spatial_bounds = (self.spatial_bins_um.min(), self.spatial_bins_um.max())

    def get_displacement_at_time_and_depth(self, times_s, locations_um, segment_index=None, grid=False):
        """Evaluate the motion estimate at times and positions

        Evaluate the motion estimate, returning the (linearly interpolated) estimated displacement
        at the given times and locations.

        Parameters
        ----------
        times_s: np.array
        locations_um: np.array
            Either this is a one-dimensional array (a vector of positions along self.dimension), or
            else a 2d array with the 2 or 3 spatial dimensions indexed along axis=1.
        segment_index: int, optional
        grid : bool
            If grid=False, the default, then times_s and locations_um should have the same one-dimensional
            shape, and the returned displacement[i] is the displacement at time times_s[i] and location
            locations_um[i].
            If grid=True, times_s and locations_um determine a grid of positions to evaluate the displacement.
            Then the returned displacement[i,j] is the displacement at depth locations_um[i] and time times_s[j].

        Returns
        -------
        displacement : np.array
            A displacement per input location, of shape times_s.shape if grid=False and (locations_um.size, times_s.size)
            if grid=True.
        """
        if self.interpolators is None:
            self.make_interpolators()

        if segment_index is None:
            if self.num_segments == 1:
                segment_index = 0
            else:
                raise ValueError("Several segment need segment_index=")

        times_s = np.asarray(times_s)
        locations_um = np.asarray(locations_um)

        if locations_um.ndim == 1:
            locations_um = locations_um
        elif locations_um.ndim == 2:
            locations_um = locations_um[:, self.dim]
        else:
            assert False

        times_s = times_s.clip(*self.temporal_bounds[segment_index])
        locations_um = locations_um.clip(*self.spatial_bounds)

        if grid:
            # construct a grid over which to evaluate the displacement
            locations_um, times_s = np.meshgrid(locations_um, times_s, indexing="ij")
            out_shape = times_s.shape
            locations_um = locations_um.ravel()
            times_s = times_s.ravel()
        else:
            # usual case: input is a point cloud
            assert locations_um.shape == times_s.shape
            assert times_s.ndim == 1
            out_shape = times_s.shape

        points = np.column_stack((times_s, locations_um))
        displacement = self.interpolators[segment_index](points)
        # reshape to grid domain shape if necessary
        displacement = displacement.reshape(out_shape)

        return displacement

    def to_dict(self):
        return dict(
            displacement=self.displacement,
            temporal_bins_s=self.temporal_bins_s,
            spatial_bins_um=self.spatial_bins_um,
            interpolation_method=self.interpolation_method,
        )

    def save(self, folder):
        folder = Path(folder)
        folder.mkdir(exist_ok=False, parents=True)

        info_file = folder / f"spikeinterface_info.json"
        info = dict(
            version=spikeinterface.__version__,
            dev_mode=spikeinterface.DEV_MODE,
            object="Motion",
            num_segments=self.num_segments,
            direction=self.direction,
            interpolation_method=self.interpolation_method,
        )
        with open(info_file, mode="w") as f:
            json.dump(check_json(info), f, indent=4)

        np.save(folder / "spatial_bins_um.npy", self.spatial_bins_um)

        for segment_index in range(self.num_segments):
            np.save(folder / f"displacement_seg{segment_index}.npy", self.displacement[segment_index])
            np.save(folder / f"temporal_bins_s_seg{segment_index}.npy", self.temporal_bins_s[segment_index])

    @classmethod
    def load(cls, folder):
        folder = Path(folder)

        info_file = folder / f"spikeinterface_info.json"
        err_msg = f"Motion.load(folder): the folder {folder} does not contain a Motion object."
        if not info_file.exists():
            raise IOError(err_msg)

        with open(info_file, "r") as f:
            info = json.load(f)
        if "object" not in info or info["object"] != "Motion":
            raise IOError(err_msg)

        direction = info["direction"]
        interpolation_method = info["interpolation_method"]
        spatial_bins_um = np.load(folder / "spatial_bins_um.npy")
        displacement = []
        temporal_bins_s = []
        for segment_index in range(info["num_segments"]):
            displacement.append(np.load(folder / f"displacement_seg{segment_index}.npy"))
            temporal_bins_s.append(np.load(folder / f"temporal_bins_s_seg{segment_index}.npy"))

        return cls(
            displacement,
            temporal_bins_s,
            spatial_bins_um,
            direction=direction,
            interpolation_method=interpolation_method,
        )

    def __eq__(self, other):
        for segment_index in range(self.num_segments):
            if not np.allclose(self.displacement[segment_index], other.displacement[segment_index]):
                return False
            if not np.allclose(self.temporal_bins_s[segment_index], other.temporal_bins_s[segment_index]):
                return False

        if not np.allclose(self.spatial_bins_um, other.spatial_bins_um):
            return False

        return True

    def copy(self):
        return Motion(
            self.displacement.copy(),
            self.temporal_bins_s.copy(),
            self.spatial_bins_um.copy(),
            interpolation_method=self.interpolation_method,
        )



def get_windows(rigid, contact_pos, spatial_bin_edges, margin_um, win_step_um, win_sigma_um, win_shape,
                zero_threshold=None):
    """
    Generate spatial windows (taper) for non-rigid motion.
    For rigid motion, this is equivalent to have one unique rectangular window that covers the entire probe.
    The windowing can be gaussian or rectangular.

    Parameters
    ----------
    rigid : bool
        If True, returns a single rectangular window
    contact_pos : np.ndarray
        Position of electrodes (num_channels, 2)
    spatial_bin_edges : np.array
        The pre-computed spatial bin edges
    margin_um : float
        The margin to extend (if positive) or shrink (if negative) the probe dimension to compute windows.=
    win_step_um : float
        The steps at which windows are defined
    win_sigma_um : float
        Sigma of gaussian window (if win_shape is gaussian)
    win_shape : float
        "gaussian" | "rect"

    Returns
    -------
    windows : 2D arrays
        The scaling for each window. Each element has num_spatial_bins values
        shape: (num_window, spatial_bins)
    window_centers: 1D np.array
        The center of each window

    Notes
    -----
    Note that kilosort2.5 uses overlaping rectangular windows.
    Here by default we use gaussian window.

    """
    bin_centers = 0.5 * (spatial_bin_edges[1:] + spatial_bin_edges[:-1])
    n = bin_centers.size

    if rigid:
        # win_shape = 'rect' is forced
        windows = [np.ones(n, dtype="float64")]
        middle = (spatial_bin_edges[0] + spatial_bin_edges[-1]) / 2.0
        window_centers = np.array([middle])
    else:
        if win_sigma_um <= win_step_um/5.:
            warnings.warn(
                f"get_windows(): spatial windows are probably not overlaping because {win_sigma_um=} and {win_step_um=}"
            )

        min_ = np.min(contact_pos) - margin_um
        max_ = np.max(contact_pos) + margin_um
        num_windows = int((max_ - min_) // win_step_um)
        border = ((max_ - min_) % win_step_um) / 2
        window_centers = np.arange(num_windows + 1) * win_step_um + min_ + border
        windows = []

        for win_center in window_centers:
            if win_shape == "gaussian":
                win = np.exp(-((bin_centers - win_center) ** 2) / (2 * win_sigma_um**2))
            elif win_shape == "rect":
                win = np.abs(bin_centers - win_center) < (win_sigma_um / 2.0)
                win = win.astype("float64")
            elif win_shape == "triangle":
                center_dist = np.abs(bin_centers - win_center)
                in_window = center_dist <= (win_sigma_um / 2.0)
                win = -center_dist
                win[~in_window] = 0
                win[in_window] -= win[in_window].min()
                win[in_window] /= win[in_window].max()
            windows.append(win)

    windows = np.array(windows)

    if zero_threshold is not None:
        windows[windows < zero_threshold] = 0
        windows /= windows.sum(axis=1, keepdims=True)

    return windows, window_centers


def get_window_domains(windows):
    """Array of windows -> list of slices where window > 0."""
    slices = []
    for w in windows:
        in_window = np.flatnonzero(w)
        slices.append(slice(in_window[0], in_window[-1] + 1))
    return slices


def scipy_conv1d(input, weights, padding="valid"):
    """SciPy translation of torch F.conv1d"""
    from scipy.signal import correlate

    n, c_in, length = input.shape
    c_out, in_by_groups, kernel_size = weights.shape
    assert in_by_groups == c_in == 1

    if padding == "same":
        mode = "same"
        length_out = length
    elif padding == "valid":
        mode = "valid"
        length_out = length - 2 * (kernel_size // 2)
    elif isinstance(padding, int):
        mode = "valid"
        input = np.pad(input, [*[(0, 0)] * (input.ndim - 1), (padding, padding)])
        length_out = length - (kernel_size - 1) + 2 * padding
    else:
        raise ValueError(f"Unknown 'padding' value of {padding}, 'padding' must be 'same', 'valid' or an integer")

    output = np.zeros((n, c_out, length_out), dtype=input.dtype)
    for m in range(n):
        for c in range(c_out):
            output[m, c] = correlate(input[m, 0], weights[c, 0], mode=mode)

    return output