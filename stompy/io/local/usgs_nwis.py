import datetime
import os
import logging
import re
import six

from six.moves import cPickle

import numpy as np
import xarray as xr
import pandas as pd
import requests

log=logging.getLogger('usgs_nwis')

from ... import utils
from .. import rdb
from .common import periods

try:
    import seawater
except ImportError:
    seawater=None


def nwis_dataset_collection(stations,*a,**k):
    """
    Fetch from multiple stations, glue together to a combined dataset.
    The rest of the options are the same as for nwis_dataset().

    Stations for which no data was found are omitted in the results.
    """
    ds_per_site=[]
    for station in stations:
        ds=nwis_dataset(station,*a,**k)
        if ds is None:
            continue
        ds['site']=('site',),[station]
        ds_per_site.append(ds)

    # And now glue those all together, but no filling of gaps yet.

    # As cases of missing data come up, this will have to get smarter about padding
    # individual sites.
    if len(ds_per_site)==0:
        # Annoying, but if no stations exist, just return None
        return None
    
    collection=xr.concat( ds_per_site, dim='site')
    for ds in ds_per_site:
        ds.close() # free up FDs
    return collection

def nwis_dataset(station,start_date,end_date,products,
                 days_per_request='M',frequency='realtime',
                 cache_dir=None,clip=True,cache_only=False,
                 cache_no_data=False):
    """
    Retrieval script for USGS waterdata.usgs.gov

    Retrieve one or more data products from a single station.
    station: string or numeric identifier for COOPS station.

    products: list of integers identifying the variable to retrieve. See
    usgs_parm_codes.tsv in the directory above this directory.

    start_date,end_date: period to retrieve, as python datetime, matplotlib datenum,
    or numpy datetime64.

    days_per_request: batch the requests to fetch smaller chunks at a time.
    if this is an integer, then chunks will start with start_date, then start_date+days_per_request,
    etc.
      if this is a string, it is interpreted as the frequency argument to pandas.PeriodIndex.
    so 'M' will request month-aligned chunks.  this has the advantage that requests for different
    start dates will still be aligned to integer periods, and can reuse cached data.

    cache_dir: if specified, save each chunk as a netcdf file in this directory,
      with filenames that include the gage, period and products.  The directory must already
      exist.

    clip: if True, then even if more data was fetched, return only the period requested.

    frequency: defaults to "realtime" which should correspond to the original
      sample frequency.  Alternatively, "daily" which access daily average values.

    cache_only: only read from cache, not attempting to fetch any new data.

    cache_no_data: periods which successfully download but contain no data are recorded 
       as empty files. Otherwise it is assumed that there may be a transient error, and 
       nothing is written to cache. Do not use this for real-time retrievals, since it may
       cache no-data results from the future.

    returns an xarray dataset.

    Note that names of variables are inferred from parameter codes where possible,
    but this is not 100% accurate with respect to the descriptions provided in the rdb,
    notably "Discharge, cubic feet per second" may be reported as
    "stream_flow_mean_daily"
    """
    start_date=utils.to_dt64(start_date)
    end_date=utils.to_dt64(end_date)

    params=dict(site_no=station,
                format='rdb')

    for prod in products:
        params['cb_%05d'%prod]='on'

    # Only for small requests of recent data:
    # base_url="https://waterdata.usgs.gov/nwis/uv"
    # Otherwise it redirects to here:
    if frequency=='realtime':
        base_url="https://nwis.waterdata.usgs.gov/usa/nwis/uv/"
    elif frequency=='daily':
        base_url="https://waterdata.usgs.gov/nwis/dv"
    else:
        raise Exception("Unknown frequency: %s"%(frequency))

    params['period']=''

    # generator for dicing up the request period
    datasets=[]

    last_url=None

    for interval_start,interval_end in periods(start_date,end_date,days_per_request):
        params['begin_date']=utils.to_datetime(interval_start).strftime('%Y-%m-%d')
        params['end_date']  =utils.to_datetime(interval_end).strftime('%Y-%m-%d')

        # This is the base name for caching, but also a shorthand for reporting
        # issues with the user, since it already encapsulates most of the
        # relevant info in a single tidy string.
        base_fn="%s_%s_%s_%s.nc"%(station,
                                  "-".join(["%d"%p for p in products]),
                                  params['begin_date'],
                                  params['end_date'])

        if cache_dir is not None:
            cache_fn=os.path.join(cache_dir,base_fn)
        else:
            cache_fn=None

        if (cache_fn is not None) and os.path.exists(cache_fn):
            log.info("Cached   %s -- %s"%(interval_start,interval_end))
            if os.path.getsize(cache_fn)==0:
                # Cached no-data result
                log.warning(" cache for %s -- %s says no-data"%(interval_start,interval_end))
                continue
            ds=xr.open_dataset(cache_fn)
        elif cache_only:
            log.info("Cache only - no data for %s -- %s"%(interval_start,interval_end))
            continue
        else:
            log.info("Fetching %s"%(base_fn))
            sesh = requests.Session()
            sesh.mount('https://', requests.adapters.HTTPAdapter(max_retries=3))
            req=sesh.get(base_url,params=params)
            data=req.text
            ds=rdb.rdb_to_dataset(text=data)
            if ds is None: # There was no data there HERE - would like to have an option to record no data
                log.warning("    %s: no data found for this period"%base_fn)
                if (cache_fn is not None) and cache_no_data:
                    log.warning("    %s: making zero-byte cache file"%base_fn)
                    with open(cache_fn,'wb') as fp: pass
                continue
            ds.attrs['url']=req.url

            if cache_fn is not None:
                ds.to_netcdf(cache_fn)

        # USGS returns data inclusive of the requested date range - leading to some overlap
        if len(datasets):
            ds=ds.isel(time=ds.time>datasets[-1].time[-1])
        datasets.append(ds)

    if len(datasets)==0:
        # could try to construct zero-length dataset, but that sounds like a pain
        # at the moment.
        log.warning("   no data for station %s for any periods!"%station)
        return None 

    if len(datasets)>1:
        # occasionally dataset have repeat timestamps. why? who knows.
        for i,ds in enumerate(datasets):
            monotonic=np.r_[True,np.diff(ds.time.values)>np.timedelta64(0,'s')]
            if np.any(~monotonic):
                datasets[i]=ds.isel(time=monotonic)
        
        try:
            dataset=xr.concat( datasets, dim='time')
        except ValueError:
            # in case not all variables appear in all datasets
            # but this is much slower.
            dataset=datasets[0]
            for other in datasets[1:]:
                dataset=dataset.combine_first(other)
        for stale in datasets:
            stale.close() # maybe free up FDs?
    else:
        dataset=datasets[0]

    if clip:
        time_sel=(dataset.time.values>=start_date) & (dataset.time.values<end_date)
        dataset=dataset.isel(time=time_sel)

    dataset.load() # force read into memory before closing files
    for d in datasets:
        d.close()

    for meta in ['datenum','tz_cd']:
        if meta in dataset.data_vars:
            dataset=dataset.set_coords(meta)
    return dataset


def add_salinity(ds):
    assert seawater is not None
    for v in ds.data_vars:
        if v.startswith('specific_conductance'):
            salt_name=v.replace('specific_conductance','salinity')
            if salt_name not in ds:
                print("%s => %s"%(v,salt_name))
                salt=seawater.eos80.salt(ds[v].values/1000. / seawater.constants.c3515,
                                         25.0, # temperature - USGS adjusts to 25degC
                                         0) # no pressure effects
                ds[salt_name]=ds[v].dims, salt

def station_metadata(station,cache_dir=None):
    if cache_dir is not None:
        cache_fn=os.path.join(cache_dir,"meta-%s.pkl"%station)

        if os.path.exists(cache_fn):
            with open(cache_fn,'rb') as fp:
                meta=cPickle.load(fp)
            return meta

    url="https://waterdata.usgs.gov/nwis/inventory?agency_code=USGS&site_no=%s"%station

    resp=requests.get(url)

    m=re.search(r"Latitude\s+([.0-9&#;']+\")",resp.text)
    lat=m.group(1)
    m=re.search(r"Longitude\s+([.0-9&#;']+\")",resp.text)
    lon=m.group(1)

    def dms_to_dd(s):
        s=s.replace('&#176;',' ').replace('"',' ').replace("'"," ").strip()
        d,m,s =[float(p) for p in s.split()]
        return d + m/60. + s/3600.
    lat=dms_to_dd(lat)
    # no mention of west longitude, but can assume it is west.
    lon=-dms_to_dd(lon)
    meta=dict(lat=lat,lon=lon)

    if cache_dir is not None:
        with open(cache_fn,'wb') as fp:
            cPickle.dump(meta,fp)
    return meta
