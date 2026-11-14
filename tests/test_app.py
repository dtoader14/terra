"""Artificial fixtures test calculations only; never served as observational data."""
from datetime import date
from pathlib import Path
import shutil

import httpx
import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from pydantic import ValidationError
from rasterio.transform import from_bounds

import app as server
from nasa import NasaClient, NasaError, VARIABLES, task_payload
from processing import colorize, file_key, grid_spec, process_bundle, read_raw, temporal_mean, unpack_and_mask

META = dict(ScaleFactor=.02, AddOffset=0, FillValueAll=[0], OrigValidMin=7500, OrigValidMax=65535)


def query(**kwargs):
    return server.Query(**(dict(variable="lst_day", start="2024-07-01", end="2024-07-31", bbox=[23,43.5,28,46.5]) | kwargs))


@pytest.mark.parametrize("kwargs", [dict(bbox=[28,43,23,46]), dict(bbox=[-180,-85,180,85]), dict(bbox=[0,0,float('nan'),1]), dict(start="2024-08-01"), dict(end="2025-08-01"), dict(variable="forecast"), dict(start="1999-01-01")])
def test_invalid_queries(kwargs):
    with pytest.raises(ValidationError):
        query(**kwargs)


def test_nasa_payload():
    payload = task_payload(query())
    assert payload["params"]["dates"] == [{"startDate":"07-01-2024","endDate":"07-31-2024"}]
    assert payload["params"]["output"]["projection"] == "geographic"
    assert payload["params"]["layers"][1]["layer"] == "QC_Day"
    ring = payload["params"]["geo"]["features"][0]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1] == [23,43.5]


def test_temperature_scale_once_and_quality():
    raw=np.array([[15000,0,16000,16000,16000]],dtype=np.float32)
    qa=np.array([[0,0,2,64,4]],dtype=np.float32)
    values=unpack_and_mask(raw,qa,META,VARIABLES["lst_day"],.02,0)
    assert values[0,0] == pytest.approx(26.85, abs=.0001)
    assert np.isnan(values[0,1:]).all()


def test_vegetation_and_missing_mean():
    metadata=dict(ScaleFactor=.0001,AddOffset=0,FillValueAll=[-3000],OrigValidMin=-2000,OrigValidMax=10000)
    values=unpack_and_mask(np.array([[5000,-3000,7000]]),np.array([[0,0,1]]),metadata,VARIABLES["ndvi"])
    assert values[0,0] == pytest.approx(.5)
    assert np.isnan(values[0,1:]).all()
    mean,count=temporal_mean([[[10,np.nan,np.nan]],[[20,30,np.nan]]])
    np.testing.assert_allclose(mean,[[15,30,np.nan]],equal_nan=True)
    assert count.tolist()==[[2,1,0]]
    rgba=np.asarray(colorize(mean,10,30,"thermal"))
    assert rgba[0,2,3]==0 and rgba[0,0,3]==255


def write_raster(path,values,nodata,scale=1):
    with rasterio.open(path,'w',driver='GTiff',height=2,width=2,count=1,dtype='uint16',crs='EPSG:4326',transform=from_bounds(23,44,24,45,2,2),nodata=nodata) as dst:
        dst.write(np.array(values,dtype='uint16'),1)
        dst.scales=[scale]


def test_qa_zero_is_not_lost_to_nodata_tag(tmp_path):
    path=tmp_path/'qa.tif'
    write_raster(path,[[0,0],[2,0]],0)
    raw,*_=read_raw(path,grid_spec([23,44,24,45],2))
    assert raw[0,0]==0  # MOD11 QC metadata declares zero fill AND good QA.


def test_bundle_render_pipeline(tmp_path):
    source=tmp_path/'source';source.mkdir();output=tmp_path/'output';output.mkdir()
    write_raster(source/'science.tif',[[15000,0],[15500,16000]],0,.02)
    write_raster(source/'quality.tif',[[0,0],[2,0]],0)
    files=[dict(file_id='science',file_name='MOD11A2.061_LST_Day_1km_doy2024185_aid0001.tif',file_size=100),dict(file_id='quality',file_name='MOD11A2.061_QC_Day_doy2024185_aid0001.tif',file_size=100)]
    class FakeNASA:
        def request(self,method,path):
            return {'files':files} if '/bundle/' in path else {'LST_Day_1km':META}
        def download(self,task,item,directory):
            dest=directory/(item['file_id']+'.tif');shutil.copyfile(source/dest.name,dest);return dest
    job=dict(variable_info=VARIABLES['lst_day'],query=query(bbox=[23,44,24,45]).model_dump(mode='json'),task_id='fixture-only')
    result=process_bundle(FakeNASA(),job,output,lambda _:None)
    assert result['frames'][0]['date']=='2024-07-03'
    assert 45<result['stats']['coverage']<55
    assert result['stats']['min']==pytest.approx(26.85,abs=.001)
    assert result['stats']['max']==pytest.approx(46.85,abs=.001)
    with rasterio.open(output/'mean.tif') as ds:
        assert ds.crs.to_epsg()==3857
        assert ds.scales==(1,)
    assert (output/'metadata.json').exists()


def test_bearer_and_cross_origin_redirect(monkeypatch,tmp_path):
    calls=[]
    def handler(request):
        calls.append(request)
        if '/api/bundle/' in request.url.path:
            return httpx.Response(302,headers={'Location':'https://example.s3.amazonaws.com/file'})
        if request.url.host=='example.s3.amazonaws.com':return httpx.Response(200,content=b'file')
        return httpx.Response(200,json=[])
    real_client=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
    client=NasaClient('test-secret')
    client.request('GET','/task')
    client.download('task',{'file_id':'file'},tmp_path)
    assert calls[0].headers['Authorization']=='Bearer test-secret'
    assert calls[1].headers['Authorization']=='Bearer test-secret'
    assert 'Authorization' not in calls[2].headers


def test_auth_expiration_is_actionable(monkeypatch):
    real_client=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(lambda _:httpx.Response(403)),**kwargs))
    with pytest.raises(NasaError,match='expired'):
        NasaClient('expired').request('GET','/task')
