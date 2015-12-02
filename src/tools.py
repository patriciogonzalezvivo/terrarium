# Author: Patricio Gonzalez Vivo - 2015 (@patriciogv)

import requests, json, math, os, sys
import numpy
from scipy.spatial import Delaunay
from PIL import Image
import xml.etree.ElementTree as ET
import shapely.geometry
import shapely.geometry.polygon 

from common import getArray, getRange, getBoundingBox, degToMeters, tileForMeters, toMercator, remap, remapPoints

TILE_SIZE = 256

def getVerticesFromTile(x,y,zoom):
    KEY = "vector-tiles-NumPyGZu-Q"
    r = requests.get(("http://vector.mapzen.com/osm/all/%i/%i/%i.json?api_key="+KEY) % (zoom,x,y))
    j = json.loads(r.text)
    p = [] # Array of points
    for layer in j:
        if layer == 'roads' or layer == 'water' or layer == 'landuse': # or layer == 'buildings':
            for features in j[layer]:
                if features == 'features':
                    for feature in j[layer][features]:
                        if feature['geometry']['type'] == 'LineString':
                            p.extend(feature['geometry']['coordinates'])                                
                        elif feature['geometry']['type'] == 'Polygon':
                            for shapes in feature['geometry']['coordinates']:
                                p.extend(shapes)
                        elif feature['geometry']['type'] == 'MultiLineString':
                            for shapes in feature['geometry']['coordinates']:
                                p.extend(shapes)
                        elif feature['geometry']['type'] == 'MultiPolygon':
                            for polygon in feature['geometry']['coordinates']:
                                for shapes in polygon:
                                    p.extend(shapes)
                                    
    return p

def getHeights(coords):
    KEY = "elevation-6va6G1Q"
    JSON = {}
    JSON['shape'] = []
    for lon,lat in coords:
        point = {}
        point['lat'] = lat
        point['lon'] = lon
        JSON['shape'].append(point)
    J = json.dumps(JSON)
    R = requests.post('http://elevation.mapzen.com/height?api_key=%s' % KEY, data=J)
    H = json.loads(R.text)['height']
    if (H):
        return H
    else:
        print("Response from elevation service, have no height",R.text)
        return []

def getTriangles(P, bbox):
    normal = [-10000,10000,-10000,10000]
    points = remapPoints(P, bbox, normal)
    delauny = Delaunay(points)
    normalize_tri = delauny.points[delauny.vertices]

    triangles = []
    for triangle in normalize_tri:
        if len(triangle) == 3:
            triangles.append(remapPoints(triangle, normal, bbox));
    return triangles

def makeHeighmap(path ,name, size, bbox, height_range, points, heights):
    total_samples = len(points)
    if total_samples != len(heights):
        print("Length don't match")
        return

    width = bbox[1]-bbox[0]
    height = bbox[3]-bbox[2]
    aspect = width/height
    
    image = Image.new("RGB", (int(size), int(size)))
    putpixel = image.putpixel
    imgx, imgy = image.size
    nx = []
    ny = []
    nr = []
    ng = []
    nb = []
    for i in range(total_samples):
        nx.append(remap(points[i][0],bbox[0],bbox[1],0,imgx))
        ny.append(remap(points[i][1],bbox[2],bbox[3],imgy,0))
        bri = int(remap(heights[i],height_range[0],height_range[1],0,255))
        nr.append(bri)
        ng.append(bri)
        nb.append(bri)
    for y in range(imgy):
        for x in range(imgx):
            dmin = math.hypot(imgx-1, imgy-1)
            j = -1
            for i in range(total_samples):
                d = math.hypot(nx[i]-x, ny[i]-y)
                if d < dmin:
                    dmin = d
                    j = i
            putpixel((x, y), (nr[j], ng[j], nb[j]))
    image.save(path+'/'+name+".png", "PNG")

def makeGeometry(triangle):
    poly = []
    for vertex in triangle:
        poly.append(tuple(vertex))

    geom = shapely.geometry.Polygon(poly)
    cw_geom = shapely.geometry.polygon.orient(geom, sign=-1)

    poly = []
    for vertex in cw_geom.exterior.coords:
        x, y = vertex
        poly.append([x, y])
    return poly

def makeGeoJson(path, name, triangles, height_range, bbox_merc):
    geoJSON = {}
    geoJSON['type'] = "FeatureCollection";
    geoJSON['features'] = [];

    element = {}
    element['type'] = "Feature"
    element['geometry'] = {}
    element['geometry']['type'] = "Polygon"
    element['geometry']['coordinates'] = []
    element['properties'] = {}
    element['properties']['kind'] = "terrain"
    if (len(height_range) > 1):
        element['properties']['min_height'] = height_range[0]
        element['properties']['max_height'] = height_range[1]
    element['properties']['bbox_merc'] = bbox_merc

    for tri in triangles:
        if len(tri) == 3:
            element['geometry']['coordinates'].append(makeGeometry(tri));
    
    geoJSON['features'].append(element);

    with open(path+'/'+name+'.json', 'w') as outfile:
        outfile.write(json.dumps(geoJSON, outfile, indent=4))
    outfile.close()

# MAKE A GEOJSON AND IMAGE for the TILE X,Y,Z
def makeTile(path, lng, lat, zoom, doPNGs):
    tile = [int(lng), int(lat), int(zoom)]

    print(" Zoom " + str(zoom) + " tile ", tile)
    name = str(tile[2])+'-'+str(tile[0])+'-'+str(tile[1])

    if os.path.isfile(path+'/'+name+".png") and os.path.isfile(path+'/'+name+".json"):
        print("Tile already created... skiping")
        return

    # Vertices
    points_latlon = getVerticesFromTile(tile[0],tile[1],tile[2])
    points_merc = toMercator(points_latlon)

    # BoundingBox
    bbox_latlon = getBoundingBox(points_latlon)
    bbox_merc = getBoundingBox(points_merc)

    # Elevation
    heights = []
    heights_range = []
    if doPNGs:
        heights = getHeights(points_latlon)
        heights_range = getRange(heights)

    # Tessellate points
    triangles = getTriangles(points_latlon, bbox_latlon)
    
    makeGeoJson(path, name, triangles, heights_range, bbox_merc)
    # showTriangles(triangles, bbox_latlon)

    # Make Heighmap
    if doPNGs:
        makeHeighmap(path, name, TILE_SIZE, bbox_merc, heights_range, points_merc, heights)

# Get all the points of a given OSM ID
def getPointsFor (osmID):
    success = False
    try:
        INFILE = 'http://www.openstreetmap.org/api/0.6/relation/'+osmID+'/full'
        print "Downloading", INFILE
        r = requests.get(INFILE)
        r.raise_for_status()
        success = True
    except Exception, e:
        print e

    if not success:
        try:
            INFILE = 'http://www.openstreetmap.org/api/0.6/way/'+osmID+'/full'
            print "Downloading", INFILE
            r = requests.get(INFILE)
            r.raise_for_status()
            success = True
        except Exception, e:
            print e

    if not success:
        try:
            INFILE = 'http://www.openstreetmap.org/api/0.6/node/'+osmID
            print "Downloading", INFILE
            r = requests.get(INFILE)
            r.raise_for_status()
            success = True
        except Exception, e:
            print e
            print "Element not found, exiting"
            sys.exit()

    # print r.encoding
    open('outfile.xml', 'w').close() # clear existing OUTFILE

    with open('outfile.xml', 'w') as fd:
      fd.write(r.text.encode("UTF-8"))
      fd.close()

    try:
        tree = ET.parse('outfile.xml')
    except Exception, e:
        print e
        print "XML parse failed, please check outfile.xml"
        sys.exit()

    root = tree.getroot()

    print "Processing:"

    points = []
    for node in root:
        if node.tag == "node":
            lat = float(node.attrib["lat"])
            lon = float(node.attrib["lon"])
            points.append({'y':lat, 'x':lon})
    return points


# Make all the tiles for points
def makeTilesFor(path, points, zoom, doPNGs):
    tiles = []

    ## find tile
    for point in points:
        tiles.append(tileForMeters(degToMeters({'x':point['x'],'y':point['y']}), zoom))

    ## de-dupe
    tiles = [dict(tupleized) for tupleized in set(tuple(item.items()) for item in tiles)]

    ## patch holes in tileset

    ## get min and max tiles for lat and long

    minx = 1048577
    maxx = -1
    miny = 1048577
    maxy = -1

    for tile in tiles:
        minx = min(minx, tile['x'])
        maxx = max(maxx, tile['x'])
        miny = min(miny, tile['y'])
        maxy = max(maxy, tile['y'])
    # print miny, minx, maxy, maxx

    newtiles = []
    for tile in tiles:
        # find furthest tiles from this tile on x and y axes
        x = tile['x']
        lessx = 1048577
        morex = -1
        y = tile['y']
        lessy = 1048577
        morey = -1
        for t in tiles:
            if int(t['x']) == int(tile['x']):
                # check on y axis
                lessy = min(lessy, t['y'])
                morey = max(morey, t['y'])
            if int(t['y']) == int(tile['y']):
                # check on x axis
                lessx = min(lessx, t['x'])
                morex = max(morex, t['x'])

        # if a tile is found which is not directly adjacent, add all the tiles between the two
        if (lessy + 2) < tile['y']:
            for i in range(int(lessy+1), int(tile['y'])):
                newtiles.append({'x':tile['x'],'y':i, 'z':zoom})
        if (morey - 2) > tile['y']:
            for i in range(int(morey-1), int(tile['y'])):
                newtiles.append({'x':tile['x'],'y':i, 'z':zoom})
        if (lessx + 2) < tile['x']:
            for i in range(int(lessx+1), int(tile['x'])):
                newtiles.append({'x':i,'y':tile['y'], 'z':zoom})
        if (morex - 2) > tile['x']:
            for i in range(int(morex-1), int(tile['x'])):
                newtiles.append({'x':i,'y':tile['y'], 'z':zoom})

    ## de-dupe
    newtiles = [dict(tupleized) for tupleized in set(tuple(item.items()) for item in newtiles)]
    ## add fill tiles to boundary tiles
    tiles = tiles + newtiles
    ## de-dupe
    tiles = [dict(tupleized) for tupleized in set(tuple(item.items()) for item in tiles)]

    ## download tiles
    print "\Makeing %i tiles at zoom level %i" % (len(tiles), zoom)

    ## make/empty the tiles folder
    if not os.path.exists(path):
        os.makedirs(path)

    total = len(tiles)
    if total == 0:
        print("Error: no tiles")
        exit()
    count = 0
    sys.stdout.write("\r%d%%" % (float(count)/float(total)*100.))
    sys.stdout.flush()
    for tile in tiles:
        makeTile(path, tile['x'], tile['y'], zoom, doPNGs)
        count += 1
        sys.stdout.write("\r%d%%" % (float(count)/float(total)*100.))
        sys.stdout.flush()

def makeTiles(path, osmID, zooms):
    points = getPointsFor(osmID)
    zoom_array = getArray(zooms)

    ## GET TILES for all zoom levels
    ##
    for zoom in zoom_array:
        makeTilesFor(path, points, zoom, True)
