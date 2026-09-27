import json, numpy as np
from osgeo import gdal, ogr
gdal.UseExceptions()
gdal.SetConfigOption('GDAL_DISABLE_READDIR_ON_OPEN','EMPTY_DIR')
d=json.load(open('/w/s2.json'))
want={'2021-05-28':'S2B_43PHK_20210528_0_L2A','2025-07-11':'S2C_43PHK_20250711_0_L2A','2026-05-27':'S2C_43PHK_20260527_0_L2A','2021-12-24':'S2B_43PHK_20211224_1_L2A','2025-12-23':'S2B_43PHK_20251223_0_L2A','2019-01-09':'S2B_43PHK_20190109_0_L2A','2026-01-17':'S2C_43PHK_20260117_0_L2A'}
feats={f['assets']['red']['href'].split('/')[-2]:f for f in d['features']}
bbox=[77.985,8.755,78.045,8.815]
# rasterize parcels (Land_id) on same grid
opts=dict(format='MEM',outputBounds=bbox,xRes=0.0001,yRes=0.0001,dstSRS='EPSG:4326')
fmb=gdal.OpenEx('/w/fmb.geojson')
ras=gdal.Rasterize('',fmb,format='MEM',outputBounds=bbox,xRes=0.0001,yRes=0.0001,outputSRS='EPSG:4326',attribute='rid',outputType=gdal.GDT_Int32,noData=0,initValues=0)
lid=ras.ReadAsArray()
vil={}
for f in json.load(open('/w/fmb.geojson'))['features']: vil[f['properties']['rid']]=f['properties']['vil_name']
res={}
for dt,sid in want.items():
    a=feats[sid]['assets']
    band=lambda k,alg='bilinear': gdal.Warp('',f"/vsicurl/{a[k]['href']}",resampleAlg=alg,**opts).ReadAsArray().astype('float32')
    red,nir=band('red'),band('nir'); scl=band('scl','near')
    ndvi=(nir-red)/(nir+red+1e-6); ok=np.isin(scl,[4,5,6,7])  # veg, bare, water, unclassified
    per={}
    for L in np.unique(lid[lid>0]):
        m=(lid==L)&ok
        if m.sum()>5: per[int(L)]=float(np.median(ndvi[m]))
    res[dt]=per
    v=np.array(list(per.values()))
    print(dt, 'parcels',len(v),'median NDVI %.3f'%np.median(v), 'share NDVI>0.35: %.1f%%'%(100*(v>0.35).mean()), 'share<0.15: %.1f%%'%(100*(v<0.15).mean()))
json.dump(res,open('/w/ndvi_res.json','w'))

import collections
W,D1,D2=res['2021-12-24'],res['2021-05-28'],res['2025-07-11']
W2,D3=res['2025-12-23'],res['2026-05-27']
ks=[k for k in W if all(k in r for r in (D1,D2,W2,D3))]
amp_pre=np.array([W[k]-D1[k] for k in ks]); amp_post=np.array([W2[k]-D3[k] for k in ks])
dry_pre=np.array([D1[k] for k in ks]); dry_post=np.array([D3[k] for k in ks])
print('dry-season median NDVI pre %.3f post %.3f'%(np.median(dry_pre),np.median(dry_post)))
print('seasonal amplitude (winter-dry) median pre %.3f post %.3f'%(np.median(amp_pre),np.median(amp_post)))
crop_pre=amp_pre>0.3; crop_post=amp_post>0.3; peren=(dry_post>0.35)
print('crop-like (amp>0.3) pre %d post %d of %d; perennial/scrub-like dry>0.35 post %d'%(crop_pre.sum(),crop_post.sum(),len(ks),peren.sum()))
print('crop-like both periods (possible continued cultivation):',(crop_pre&crop_post).sum())
print('by village (continued cultivation):',collections.Counter(vil[k] for k,a,b in zip(ks,crop_pre,crop_post) if a and b))
