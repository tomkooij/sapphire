""" Fetch events and other data from the event summary data (ESD)

    This module enables you to access the event summary data.

    If you are in a real hurry and know what you're doing (and took the
    time to read this far), you can call the :func:`quick_download`
    function like this::

        >>> import sapphire.esd
        >>> data = sapphire.esd.quick_download(501)

    For regular use, look up :func:`download_data`.

"""
import urllib2
import urllib
import csv
import os.path
import calendar
import time
import datetime
import itertools

import tables
import progressbar


EVENTS_URL = 'http://data.hisparc.nl/data/{station_number:d}/events?{query}'
WEATHER_URL = 'http://data.hisparc.nl/data/{station_number:d}/weather?{query}'


def quick_download(station_id, date=None):
    """Quickly download some data

    :param station_id: The HiSPARC station number
    :param date: the date for which to get data (datetime.datetime
        instance), passed unchanged to the :func:`download_data` as
        :param start:.
    :returns: handle to an open PyTables file

    Everything is handled by this function, including file creation.
    Expect no frills: you just get yesterday's data.

    Example usage::

        >>> import sapphire.esd
        >>> data = sapphire.esd.quick_download(501)
        >>> print data
        data1.h5 (File) u''
        Last modif.: 'Mon Jun  9 22:03:50 2014'
        Object Tree:
        / (RootGroup) u''
        /s501 (Group) u''
        /s501/events (Table(58898,)) ''

    """
    path = _first_available_numbered_path()
    data = tables.open_file(path, 'w')
    download_data(data, None, station_id, date)
    return data


def _first_available_numbered_path():
    """Find first available file name in sequence

    If data1.h5 is taken, return data2.h5, etc.

    """
    path = 'data%d.h5'
    return next(path % idx for idx in itertools.count(start=1)
                if not os.path.exists(path % idx))


def download_data(file, group, station_number, start=None, end=None,
                  type='events'):
    """Download event summary data

    :param file: The PyTables datafile handler
    :param group: The PyTables destination group, which need not exist
    :param station_number: The HiSPARC station number for which to get data
    :param start: a datetime instance defining the start of the search
        interval
    :param end: a datetime instance defining the end of the search
        interval
    :param type: the datatype to download, either 'events' or 'weather'.

    If group is None, use '/s<station_id>' as a default.

    The start and stop parameters may both be None.  In that case,
    yesterday's data is downloaded.  If only end is None, a single day's
    worth of data is downloaded, starting at the datetime specified with
    start.

    Example::

        >>> import tables
        >>> import datetime
        >>> import sapphire.esd
        >>> data = tables.open_file('data.h5', 'w')
        >>> sapphire.esd.download_data(data, '/s501', 501,
        ...     datetime.datetime(2013, 9, 1), datetime.datetime(2013, 9, 2))

    """
    # sensible default for group name
    if group is None:
        group = '/s%d' % station_id

    # sensible defaults for start and end
    if start is None:
        if end is not None:
            raise RuntimeError("Start is None, but end is not. "
                               "I can't go on like this.")
        else:
            yesterday = datetime.date.today() - datetime.timedelta(days=1)
            start = datetime.datetime.combine(yesterday, datetime.time(0, 0))
    if end is None:
        end = start + datetime.timedelta(days=1)

    # build and open url, create tables and set read function
    query = urllib.urlencode({'start': start, 'end': end})
    if type == 'events':
        url = EVENTS_URL.format(station_number=station_number, query=query)
        table = _create_table(file, group)
        read_and_store = _read_line_and_store_event
    elif type == 'weather':
        url = WEATHER_URL.format(station_number=station_number, query=query)
        table = _create_weather_table(file, group)
        read_and_store = _read_line_and_store_weather
    else:
        raise ValueError("Data type not recognized.")

    data = urllib2.urlopen(url)

    # keep track of event timestamp within [start, end] interval for
    # progressbar
    t_start = calendar.timegm(start.utctimetuple())
    t_end = calendar.timegm(end.utctimetuple())
    t_delta = t_end - t_start
    pbar = progressbar.ProgressBar(maxval=1.,
                                   widgets=[progressbar.Percentage(),
                                            progressbar.Bar(),
                                            progressbar.ETA()]).start()

    # loop over lines in csv as they come streaming in
    prev_update = time.time()
    reader = csv.reader(data, delimiter='\t')
    for line in reader:
        timestamp = read_and_store(line, table)
        # update progressbar every .5 seconds
        if time.time() - prev_update > .5 and not timestamp == 0.:
            pbar.update((1. * timestamp - t_start) / t_delta)
            prev_update = time.time()
    pbar.finish()


def _create_table(file, group):
    """Create event table in PyTables file

    Create an event table containing the ESD data columns which are
    available in the CSV download.

    :param file: PyTables file
    :param group: the group to contain the events table, which need not
                  exist

    """
    description = {'event_id': tables.UInt32Col(pos=0),
                   'timestamp': tables.Time32Col(pos=1),
                   'nanoseconds': tables.UInt32Col(pos=2),
                   'ext_timestamp': tables.UInt64Col(pos=3),
                   'pulseheights': tables.Int16Col(pos=4, shape=4),
                   'integrals': tables.Int32Col(pos=5, shape=4),
                   'n1': tables.Float32Col(pos=6),
                   'n2': tables.Float32Col(pos=7),
                   'n3': tables.Float32Col(pos=8),
                   'n4': tables.Float32Col(pos=9),
                   't1': tables.Float32Col(pos=10),
                   't2': tables.Float32Col(pos=11),
                   't3': tables.Float32Col(pos=12),
                   't4': tables.Float32Col(pos=13),
                   't_trigger': tables.Float32Col(pos=14)}

    return file.create_table(group, 'events', description, createparents=True)


def _create_weather_table(file, group):
    """Create weather table in PyTables file

    Create a weather table containing the ESD weather columns which are
    available in the CSV download.

    :param file: PyTables file
    :param group: the group to contain the weather table, which need not
                  exist

    """
    description = {'event_id': tables.UInt32Col(pos=0),
                   'timestamp': tables.Time32Col(pos=1),
                   'temp_inside': tables.Float32Col(pos=2),
                   'temp_outside': tables.Float32Col(pos=3),
                   'humidity_inside': tables.Int16Col(pos=4),
                   'humidity_outside': tables.Int16Col(pos=5),
                   'barometer': tables.Float32Col(pos=6),
                   'wind_dir': tables.Int16Col(pos=7),
                   'wind_speed': tables.Int16Col(pos=8),
                   'solar_rad': tables.Int16Col(pos=9),
                   'uv': tables.Int16Col(pos=10),
                   'evapotranspiration': tables.Float32Col(pos=11),
                   'rain_rate': tables.Float32Col(pos=12),
                   'heat_index': tables.Int16Col(pos=13),
                   'dew_point': tables.Float32Col(pos=14),
                   'wind_chill': tables.Float32Col(pos=15)}

    return file.create_table(group, 'weather', description, createparents=True)


def _read_line_and_store_event(line, table):
    """Read CSV line and store event

    Read a line from the CSV download and store event.  Return the event
    timestamp to keep track of the progress.

    :param line: text line from the CSV file
    :param table: pytables table for event storage
    :return: event timestamp

    """
    # ignore comment lines
    if line[0][0] == '#':
        return 0.

    # break up CSV line
    (date, time_str, timestamp, nanoseconds, ph1, ph2, ph3, ph4, int1,
     int2, int3, int4, n1, n2, n3, n4, t1, t2, t3, t4, t_trigger) = line

    # convert string values to correct data types or calculate values
    event_id = len(table)
    timestamp = int(timestamp)
    nanoseconds = int(nanoseconds)
    ext_timestamp = timestamp * 1000000000 + nanoseconds
    pulseheights = [int(ph1), int(ph2), int(ph3), int(ph4)]
    integrals = [int(int1), int(int2), int(int3), int(int4)]
    n1 = float(n1)
    n2 = float(n2)
    n3 = float(n3)
    n4 = float(n4)
    t1 = float(t1)
    t2 = float(t2)
    t3 = float(t3)
    t4 = float(t4)
    t_trigger = float(t_trigger)

    # store event
    table.append([[event_id, timestamp, nanoseconds, ext_timestamp,
                   pulseheights, integrals, n1, n2, n3, n4, t1, t2,
                   t3, t4, t_trigger]])

    return timestamp


def _read_line_and_store_weather(line, table):
    """Read CSV line and store weather data

    Read a line from the CSV download and store weather.  Return the
    weather timestamp to keep track of the progress.

    :param line: text line from the CSV file
    :param table: pytables table for weather storage
    :return: weather timestamp

    """
    # ignore comment lines
    if line[0][0] == '#':
        return 0.

    # break up CSV line

    (date, time, timestamp, temperature_inside, temperature_outside,
     humidity_inside, humidity_outside, atmospheric_pressure,
     wind_direction, wind_speed, solar_radiation, uv_index,
     evapotranspiration, rain_rate, heat_index, dew_point, wind_chill) = line

    # convert string values to correct data types
    event_id = len(table)
    timestamp = int(timestamp)
    temp_inside = float(temperature_inside)
    temp_outside = float(temperature_outside)
    humidity_inside = int(humidity_inside)
    humidity_outside = int(humidity_outside)
    barometer = float(atmospheric_pressure)
    wind_dir = int(wind_direction)
    wind_speed = int(wind_speed)
    solar_rad = int(solar_radiation)
    uv = int(uv_index)
    evapotranspiration = float(evapotranspiration)
    rain_rate = float(rain_rate)
    heat_index = int(heat_index)
    dew_point = float(dew_point)
    wind_chill = float(wind_chill)

    # store event
    table.append([[event_id, timestamp, temp_inside, temp_outside,
                   humidity_inside, humidity_outside, barometer, wind_dir,
                   wind_speed, solar_rad, uv, evapotranspiration, rain_rate,
                   heat_index, dew_point, wind_chill]])

    return timestamp
