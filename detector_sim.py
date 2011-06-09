""" HiSPARC detector simulation

    This simulation takes an Extended Air Shower simulation ground
    particles file and uses that to simulate numerous showers hitting a
    HiSPARC detector station.  Only data of one shower is used, but by
    randomly selecting points on the ground as the position of a station,
    the effect of the same shower hitting various positions around the
    station is simulated.

"""
from __future__ import division

import tables
import csv
import numpy as np
from numpy import nan
from math import pi, sqrt, sin, cos, atan2, isinf
import gzip
import progressbar as pb
import os.path

import clusters
import storage


DATAFILE = 'data-e15.h5'


def generate_positions(R, num):
    """Generate positions and an orientation uniformly on a circle

    :param R: radius of circle
    :param num: number of positions to generate

    :return: r, phi, alpha

    """
    for i in range(num):
        phi, alpha = np.random.uniform(-pi, pi, 2)
        r = np.sqrt(np.random.uniform(0, R ** 2))
        yield r, phi, alpha

def get_station_coordinates(station, r, phi, alpha):
    """Calculate coordinates of a station given cluster coordinates

    :param station: station definition
    :param r, phi: polar coordinates of cluster center
    :param alpha: rotation of cluster

    :return: x, y, alpha; coordinates and rotation of station relative to
        absolute coordinate system

    """
    X = r * cos(phi)
    Y = r * sin(phi)

    sx, sy = station.position
    xp = sx * cos(alpha) - sy * sin(alpha)
    yp = sx * sin(alpha) + sy * cos(alpha)

    x = X + xp
    y = Y + yp
    angle = alpha + station.angle

    return x, y, angle

def get_station_particles(station, data, X, Y, alpha):
    """Return all particles hitting a station

    :param station: station definition
    :param data: HDF5 particle dataset
    :param X, Y: coordinates of station center
    :param alpha: rotation angle of station

    :return: list of detectors containing list of particles
    :rtype: list of lists

    """
    particles = []

    size = station.detector_size
    for detector in station.detectors:
        x, y, orientation = detector
        particles.append(get_detector_particles(data, X, Y, x, y, size,
                                                orientation, alpha))
    return particles

def get_detector_particles(data, X, Y, x, y, size, orientation,
                           alpha=None):
    """Return all particles hitting a single detector

    Given a HDF5 table containing information on all simulated particles
    and coordinates and orientation of a detector, search for all
    particles which have hit the detector.

    :param data: table containing particle data
    :param X, Y: X, Y coordinates of center of station
    :param x, y: x, y coordinates of center of detector relative to
        station center 
    :param size: tuple (width, length) giving detector size
    :param orientation: either 'UD' or 'LR', for up-down or left-right
        detector orientations, relative to station
    :param alpha: rotation angle of entire station

    :return: list of particles which have hit the detector

    """
    c = get_detector_corners(X, Y, x, y, size, orientation, alpha)

    # determine equations describing detector edges
    b11, line1, b12 = get_line_boundary_eqs(c[0], c[1], c[2])
    b21, line2, b22 = get_line_boundary_eqs(c[1], c[2], c[3])

    # particles satisfying all equations are inside the detector
    return data.readWhere("(b11 < %s) & (%s < b12) & "
                          "(b21 < %s) & (%s < b22)" % (line1, line1,
                                                       line2, line2))

def get_line_boundary_eqs(p0, p1, p2):
    """Get line equations using three points

    Given three points, this function computes the equations for two
    parallel lines going through these points.  The first and second point
    are on the same line, whereas the third point is taken to be on a
    line which runs parallel to the first.  The return value is an
    equation and two boundaries which can be used to test if a point is
    between the two lines.

    :param p0, p1: (x, y) tuples on the same line
    :param p2: (x, y) tuple on the parallel line

    :return: value1, equation, value2, such that points satisfying value1
        < equation < value2 are between the parallel lines

    Example::

        >>> get_line_boundary_eqs((0, 0), (1, 1), (0, 2))
        (0.0, 'y - 1.000000 * x', 2.0)

    """
    (x0, y0), (x1, y1), (x2, y2) = p0, p1, p2

    # First, compute the slope
    a = (y1 - y0) / (x1 - x0)

    # Calculate the y-intercepts of both lines
    b1 = y0 - a * x0
    b2 = y2 - a * x2

    # Compute the general equation for the lines
    if not isinf(a):
        line = "y - %f * x" % a
    else:
        # line is exactly vertical
        line = "x"
        b1, b2 = x0, x2

    # And order the y-intercepts
    if b1 > b2:
        b1, b2 = b2, b1

    return b1, line, b2

def get_detector_corners(X, Y, x, y, size, orientation, alpha=None):
    """Get the x, y coordinates of the detector corners

    :param X, Y: X, Y coordinates of center of station
    :param x, y: x, y coordinates of center of detector relative to
        station center 
    :param size: tuple (width, length) giving detector size
    :param orientation: either 'UD' or 'LR', for up-down or left-right
        detector orientations, relative to station
    :param alpha: rotation angle of entire station

    :return: x, y coordinates of detector corners
    :rtype: list of (x, y) tuples

    """
    dx = size[0] / 2
    dy = size[1] / 2

    if orientation == 'UD':
        corners = [(x - dx, y - dy), (x + dx, y - dy), (x + dx, y + dy),
                   (x - dx, y + dy)]
    elif orientation == 'LR':
        corners = [(x - dy, y - dx), (x + dy, y - dx), (x + dy, y + dx),
                   (x - dy, y + dx)]
    else:
        raise Exception("Unknown detector orientation: %s" % orientation)

    if alpha is not None:
        sina = sin(alpha)
        cosa = cos(alpha)
        corners = [[x * cosa - y * sina, x * sina + y * cosa] for x, y in
                   corners]

    return [(X + x, Y + y) for x, y in corners]

def do_simulation(cluster, data, grdpcles, output, R, N):
    """Perform a simulation

    :param cluster: BaseCluster (or derived) instance
    :param data: the HDF5 file
    :param grdpcles: name of the dataset containing the ground particles
    :param output: name of the destination to store results
    :param R: maximum distance of shower to center of cluster
    :param N: number of simulations to perform

    """
    try:
        grdpcles = data.getNode('/', grdpcles)
    except tables.NoSuchNodeError:
        print "Cancelling simulation; %s not found in tree." % grdpcles
        return

    head, tail = os.path.split(output)
    try:
        data.createGroup(head, tail, createparents=True)
    except tables.NodeError:
        print "Cancelling simulation; %s already exists?" % output
        return

    print 74 * '-'
    print """Running simulation

Ground particles:   %s
Output destination: %s

Maximum core distance of cluster center:   %f m
Number of cluster positions in simulation: %d
    """ % (grdpcles._v_pathname, output, R, N)

    s_events = data.createTable(output, 'headers',
                                storage.SimulationHeader)
    p_events = data.createTable(output, 'particles',
                                storage.ParticleEvent)

    progress = pb.ProgressBar(maxval=N, widgets=[pb.Percentage(),
                                                 pb.Bar(), pb.ETA()])
    for event_id, (r, phi, alpha) in \
        progress(enumerate(generate_positions(R, N))):
        write_header(s_events, event_id, 0, r, phi, alpha)
        for station_id, station in enumerate(cluster.stations, 1):
            x, y, beta = get_station_coordinates(station, r, phi, alpha)
            # calculate station r, phi just to save it in header
            s_r = sqrt(x ** 2 + y ** 2)
            s_phi = atan2(y, x)
            write_header(s_events, event_id, station_id, s_r, s_phi, beta)

            plist = get_station_particles(station, grdpcles, x, y, beta)
            write_detector_particles(p_events, event_id, station_id,
                                     plist)

    s_events.flush()
    p_events.flush()

    print 74 * '-'
    print

def write_header(table, event_id, station_id, r, phi, alpha):
    """Write simulation event header information to file

    :param table: HDF5 table
    :param event_id: simulation event id
    :param station_id: station id inside cluster, 0 for cluster header
    :param r, phi: r, phi for cluster or station position, both as
        absolute coordinates
    :param alpha: cluster rotation angle or station rotation angle

    """
    row = table.row
    row['id'] = event_id
    row['station_id'] = station_id
    row['r'] = r
    row['phi'] = phi
    row['alpha'] = alpha
    row.append()
    table.flush()

def write_detector_particles(table, event_id, station_id, plist):
    """Write particles to file

    :param table: HDF5 table
    :param event_id: simulation event id
    :param station_id: station id inside cluster
    :param plist: list of detectors, containing list of particles

    """
    row = table.row
    for detector_id, detector in enumerate(plist):
        for particle in detector:
            row['id'] = event_id
            row['station_id'] = station_id
            row['detector_id'] = detector_id
            row['pid'] = particle['pid']
            row['r'] = particle['core_distance']
            row['phi'] = particle['polar_angle']
            row['time'] = particle['arrival_time']
            row['energy'] = particle['energy']
            row.append()
    table.flush()

def store_observables(data, group):
    """Analyze simulation results and store derived data

    Loop through simulation result tables and find observables like the
    number of particles which hit detectors, as well as the arrival time
    of the first particle to hit a detector.  The number of detectors
    which have particles are also recorded.  Finally the per shower
    results from all stations are combined and stored as a coincidence
    event.

    To speed things up, pointers into the two simulation result tables are
    advanced row by row.  Event and station id's are continually checked
    to now when to break.  The flow is a bit complicated, but it is fast.

    :param data: the HDF5 file
    :param group: name of the group which contains the simulation and will
        hold the observables and coincidence data

    """
    try:
        group = data.getNode('/', group)
    except tables.NoSuchNodeError:
        print "Cancelling; %s not found in tree." % group
        return

    try:
        obs = data.createTable(group, 'observables',
                               storage.ObservableEvent)
        coinc = data.createTable(group, 'coincidences',
                                 storage.CoincidenceEvent)
    except tables.NodeError:
        print "Cancelling; %s already exists?" % \
            os.path.join(group._v_pathname, 'observables')
        return

    headers = data.getNode(group, 'headers')
    particles = data.getNode(group, 'particles')

    print "Storing observables from %s" % group._v_pathname

    obs_row = obs.row
    coinc_row = coinc.row
    progress = pb.ProgressBar(maxval=len(headers),
                              widgets=[pb.Percentage(), pb.Bar(),
                                       pb.ETA()]).start()
    headers = iter(headers)
    particles = iter(particles)

    # start with first rows initialized
    header = headers.next()
    particle = particles.next()
    # loop over events
    while True:
        assert header['station_id'] == 0
        # freeze header row for later use
        event = header.fetch_all_fields()

        # N = number of stations which trigger
        N = 0
        try:
            # loop over headers
            while True:
                header = headers.next()
                # if station_id == 0, we have a new event, not a station
                if header['station_id'] == 0:
                    break
                else:
                    t = [[], [], [], []]
                    # loop over particles
                    while True:
                        # check if particle matches current event/station
                        if particle['id'] != event['id'] or \
                            particle['station_id'] != \
                            header['station_id']:
                            break
                        else:
                            t[particle['detector_id']].append(particle['time'])
                            try:
                                particle = particles.next()
                            except StopIteration:
                                # Ran out of particles. Forcing invalid
                                # id, so that higher-level loops can
                                # finish business, but no new particles
                                # will be processed
                                particle['id'] = -1
                    write_observables(obs_row, header, t)
                    # trigger if Ndet hit >= 2
                    if sum([1 if u else 0 for u in t]) >= 2:
                        N += 1
        # StopIteration when we run out of headers
        except StopIteration:
            break
        finally:
            write_coincidence(coinc_row, event, N)
            progress.update(header.nrow + 1)

    obs.flush()
    coinc.flush()
    print

def write_observables(row, station, t):
    row['id'] = station['id']
    row['station_id'] = station['station_id']
    row['r'] = station['r']
    row['phi'] = station['phi']
    row['alpha'] = station['alpha']
    row['N'] = sum([1 if u else 0 for u in t])
    row['t1'], row['t2'], row['t3'], row['t4'] = \
        [min(u) if len(u) else nan for u in t]
    row['n1'], row['n2'], row['n3'], row['n4'] = \
        [len(u) for u in t]
    row.append()

def write_coincidence(row, event, N):
    row['id'] = event['id']
    row['N'] = N
    row['r'] = event['r']
    row['phi'] = event['phi']
    row['alpha'] = event['alpha']
    row.append()


if __name__ == '__main__':
    try:
        data
    except NameError:
        data = tables.openFile(DATAFILE, 'a')

    sim = 'E_1PeV/zenith_0'
    cluster = clusters.SimpleCluster()
    do_simulation(cluster, data, os.path.join('/showers', sim, 'leptons'),
                  os.path.join('/simulations', sim), R=100, N=100)
    store_observables(data, os.path.join('/simulations', sim))
