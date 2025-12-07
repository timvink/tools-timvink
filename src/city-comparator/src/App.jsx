import React, { useState, useRef, useEffect } from 'react';

// --- Geometry & Projection Helpers ---

const degToRad = (deg) => (deg * Math.PI) / 180;

// Helper to check if a structure is a MultiPolygon (array of arrays of coords)
const isMultiPolygon = (coords) => Array.isArray(coords[0]) && Array.isArray(coords[0][0]);

const getCentroid = (coords) => {
  let latSum = 0;
  let lngSum = 0;
  let count = 0;

  const addPoints = (points) => {
    points.forEach((pt) => {
      latSum += pt[0];
      lngSum += pt[1];
      count++;
    });
  };

  if (isMultiPolygon(coords)) {
    coords.forEach(polygon => addPoints(polygon));
  } else {
    addPoints(coords);
  }
  
  return [latSum / count, lngSum / count];
};

// Recursively projects coordinates to maintain physical size
const projectCoords = (coords, origCenLat, origCenLng, targetLat, targetLng) => {
  // Base case: Single coordinate pair [lat, lng]
  if (typeof coords[0] === 'number') {
    const lat = coords[0];
    const lng = coords[1];

    // 1. Calculate offset from original center
    const latOffset = lat - origCenLat;
    const lngOffset = lng - origCenLng;

    // 2. Convert Longitude offset to "metric equivalent" at original latitude
    const metricFactorOrig = Math.cos(degToRad(origCenLat));
    const metricWidth = lngOffset * metricFactorOrig;

    // 3. Convert "metric equivalent" back to degrees at TARGET latitude
    const metricFactorTarget = Math.cos(degToRad(targetLat));
    // Prevent division by zero or extreme distortion near poles
    const safeFactor = Math.max(0.1, metricFactorTarget); 
    const newLngOffset = metricWidth / safeFactor;

    return [targetLat + latOffset, targetLng + newLngOffset];
  }

  // Recursive case: Array of coordinates (Polygon or MultiPolygon ring)
  return coords.map(item => projectCoords(item, origCenLat, origCenLng, targetLat, targetLng));
};

const projectPolygonToLocation = (originalCoords, targetLat, targetLng) => {
  const [origCenLat, origCenLng] = getCentroid(originalCoords);
  return projectCoords(originalCoords, origCenLat, origCenLng, targetLat, targetLng);
};

// --- Detailed City Data ---
const CITY_DATA = {
  amsterdam: {
    name: "Amsterdam",
    color: "#e67e22", 
    center: [52.3676, 4.9041],
    zoom: 11,
    description: "Municipality (~219 km²)",
    // Detailed shape approx A10 + North
    coords: [
       [52.424, 4.885], [52.428, 4.920], [52.415, 4.965], [52.395, 5.000], [52.380, 5.015],
       [52.360, 5.020], [52.340, 5.000], [52.325, 4.970], [52.310, 4.930], [52.295, 4.880],
       [52.305, 4.830], [52.325, 4.790], [52.345, 4.760], [52.370, 4.755], [52.390, 4.775],
       [52.405, 4.820], [52.415, 4.850]
    ]
  },
  london: {
    name: "London",
    color: "#e74c3c", 
    center: [51.5072, -0.1276],
    zoom: 9,
    description: "Greater London (~1,572 km²)",
    // Detailed shape approx Greater London Boundary
    coords: [
       [51.669, -0.040], [51.625, 0.160], [51.590, 0.230], [51.550, 0.280], [51.500, 0.310],
       [51.450, 0.220], [51.380, 0.150], [51.320, 0.100], [51.290, -0.120], [51.310, -0.250],
       [51.360, -0.380], [51.410, -0.480], [51.500, -0.510], [51.550, -0.480], [51.620, -0.300],
       [51.660, -0.150]
    ]
  },
  nyc: {
    name: "New York City",
    color: "#3498db", 
    center: [40.7128, -74.0060],
    zoom: 10,
    description: "Five Boroughs (~783 km²)",
    // MultiPolygon: 1. Manhattan, 2. Staten Island, 3. Brooklyn/Queens/Bronx (Simplified as one connected landmass for visual clarity or split)
    coords: [
       // Manhattan
       [[40.875, -73.910], [40.820, -73.960], [40.750, -74.010], [40.700, -74.020], [40.705, -73.970], [40.740, -73.965], [40.800, -73.930], [40.840, -73.910]],
       // Staten Island
       [[40.640, -74.060], [40.650, -74.200], [40.560, -74.250], [40.500, -74.240], [40.520, -74.150], [40.590, -74.050]],
       // Bronx, Queens, Brooklyn
       [[40.910, -73.900], [40.870, -73.780], [40.780, -73.750], [40.750, -73.700], 
        [40.600, -73.740], [40.570, -73.850], [40.570, -74.020], [40.650, -74.040], 
        [40.720, -73.940], [40.800, -73.900], [40.840, -73.880], [40.880, -73.920]]
    ]
  }
};

export default function CityComparisonApp() {
  const mapContainerRef = useRef(null);
  const mapInstanceRef = useRef(null);
  const layerGroupRef = useRef(null);
  const [isLeafletLoaded, setIsLeafletLoaded] = useState(false);

  const [baseCityKey, setBaseCityKey] = useState('london');
  const [overlayCityKey, setOverlayCityKey] = useState(null); 
  
  // Track the CURRENT position of the overlay city
  const [overlayCoords, setOverlayCoords] = useState(null);

  // --- 1. Load Leaflet ---
  useEffect(() => {
    // Robust check for Leaflet presence
    if (window.L && typeof window.L.map === 'function') {
      setIsLeafletLoaded(true);
      return;
    }

    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
    document.head.appendChild(link);

    const script = document.createElement("script");
    script.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
    script.async = true;
    script.onload = () => setIsLeafletLoaded(true);
    document.body.appendChild(script);

    // Optional cleanup of script tags isn't strictly necessary for app lifecycle, 
    // but good to know this runs once.
  }, []);

  // --- 2. Initialize Map ---
  useEffect(() => {
    if (!isLeafletLoaded || !mapContainerRef.current) return;
    
    // Cleanup existing map if it exists (fixes React Strict Mode double-init)
    if (mapInstanceRef.current) {
      mapInstanceRef.current.remove();
      mapInstanceRef.current = null;
    }

    const L = window.L;
    // Initialize map
    const map = L.map(mapContainerRef.current, {
        zoomControl: false,
        attributionControl: false 
    }).setView(CITY_DATA[baseCityKey].center, CITY_DATA[baseCityKey].zoom);

    // CartoDB Positron (Cleaner, light map)
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
      subdomains: 'abcd',
      maxZoom: 19
    }).addTo(map);
    
    L.control.attribution({ position: 'bottomright' }).addTo(map);

    const layerGroup = L.layerGroup().addTo(map);
    layerGroupRef.current = layerGroup;
    mapInstanceRef.current = map;

    // Initial Draw
    drawMap();

    // Cleanup function when component unmounts
    return () => {
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove();
        mapInstanceRef.current = null;
      }
    };
  }, [isLeafletLoaded]);

  // --- 3. Logic to Handle Selection Changes ---

  // When Base City changes: Fly to it
  useEffect(() => {
    if (!mapInstanceRef.current) return;
    const city = CITY_DATA[baseCityKey];
    mapInstanceRef.current.flyTo(city.center, city.zoom, { duration: 1.5 });
    
    if (overlayCityKey) {
       const overlayCityData = CITY_DATA[overlayCityKey];
       const newProjected = projectPolygonToLocation(
         overlayCityData.coords,
         city.center[0],
         city.center[1]
       );
       setOverlayCoords(newProjected);
    }
  }, [baseCityKey]);

  // When Overlay City changes: Reset its position to the Base City center
  useEffect(() => {
    if (!overlayCityKey) {
        setOverlayCoords(null);
        return;
    }
    const baseCenter = CITY_DATA[baseCityKey].center;
    const overlayCityData = CITY_DATA[overlayCityKey];
    
    // Project the overlay city's original shape to the Base City's location
    const newProjected = projectPolygonToLocation(
      overlayCityData.coords,
      baseCenter[0],
      baseCenter[1]
    );
    setOverlayCoords(newProjected);
  }, [overlayCityKey]);


  // --- 4. Drawing Logic ---
  
  useEffect(() => {
     drawMap();
  }, [isLeafletLoaded, baseCityKey, overlayCoords]);

  const drawMap = () => {
    if (!mapInstanceRef.current || !layerGroupRef.current || !window.L) return;
    const L = window.L;
    const group = layerGroupRef.current;
    
    group.clearLayers();

    // -- Draw Base City (Static, Reference) --
    const baseData = CITY_DATA[baseCityKey];
    // Base city always uses its original real-world coords
    L.polygon(baseData.coords, {
        color: '#333', 
        weight: 2,
        opacity: 0.6,
        fillColor: '#999',
        fillOpacity: 0.1,
        dashArray: '5, 10' // Dashed line for reference
    }).addTo(group);


    // -- Draw Overlay City (Draggable) --
    if (overlayCoords && overlayCityKey) {
        const overlayData = CITY_DATA[overlayCityKey];
        
        // 1. Draw the Polygon (Visual)
        const poly = L.polygon(overlayCoords, {
            color: overlayData.color,
            weight: 3,
            opacity: 1,
            fillColor: overlayData.color,
            fillOpacity: 0.35
        }).addTo(group);

        // 2. Draw Handle (for dragging)
        const center = getCentroid(overlayCoords);
        
        // Inline SVG for the handle to avoid dependency issues
        const iconHtml = `
          <div style="
            background-color: ${overlayData.color}; 
            width: 32px; height: 32px; 
            border-radius: 50%; 
            border: 3px solid white; 
            box-shadow: 0 4px 6px rgba(0,0,0,0.3); 
            display: flex; align-items: center; justify-content: center; 
            color: white; 
            cursor: grab;
          ">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 9l-3 3 3 3M9 5l3-3 3 3M19 9l3 3-3 3M9 19l3 3 3-3"/><circle cx="12" cy="12" r="3"/></svg>
          </div>
        `;

        const customIcon = L.divIcon({
            className: 'custom-drag-icon',
            html: iconHtml,
            iconSize: [32, 32],
            iconAnchor: [16, 16]
        });

        const marker = L.marker(center, {
            draggable: true,
            icon: customIcon,
            zIndexOffset: 1000
        }).addTo(group);

        // Drag Logic
        marker.on('drag', (e) => {
            const newLatLng = e.target.getLatLng();
            // Project based on the ORIGINAL data shape to prevent distortion accumulation
            const newShape = projectPolygonToLocation(
                CITY_DATA[overlayCityKey].coords,
                newLatLng.lat,
                newLatLng.lng
            );
            poly.setLatLngs(newShape);
        });

        marker.on('dragend', (e) => {
             const newLatLng = e.target.getLatLng();
             const newShape = projectPolygonToLocation(
                CITY_DATA[overlayCityKey].coords,
                newLatLng.lat,
                newLatLng.lng
            );
            setOverlayCoords(newShape);
        });
    }
  };

  // Inline SVGs for UI to remove dependencies
  const MapIcon = () => (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"></polygon><line x1="8" y1="2" x2="8" y2="18"></line><line x1="16" y1="6" x2="16" y2="22"></line></svg>
  );

  const InfoIcon = () => (
    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-blue-500 shrink-0 mt-0.5"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
  );

  return (
    <div className="flex flex-col h-screen w-full bg-slate-50 font-sans overflow-hidden">
      <style>{`
        .custom-drag-icon {
          background: transparent;
          border: none;
        }
        .leaflet-container {
            background-color: #f0f3f5;
        }
      `}</style>

      {/* Top Controls Bar */}
      <div className="bg-white border-b shadow-sm p-4 z-10 flex flex-col md:flex-row items-center justify-between gap-4">
         <div className="flex items-center gap-3">
             <div className="bg-blue-600 text-white p-2 rounded-lg shadow-sm">
                <MapIcon />
             </div>
             <div>
                 <h1 className="text-lg font-bold text-slate-800 leading-tight">Urban Scale Comparator</h1>
                 <p className="text-xs text-slate-500">Corrects for Mercator distortion automatically</p>
             </div>
         </div>

         <div className="flex items-center gap-4 bg-slate-100 p-2 rounded-lg border border-slate-200">
             {/* Base City Selector */}
             <div className="flex flex-col">
                 <label className="text-[10px] uppercase font-bold text-slate-400 tracking-wider mb-1">Reference (Fixed)</label>
                 <div className="flex gap-1">
                    {Object.keys(CITY_DATA).map(key => (
                        <button
                            key={key}
                            onClick={() => setBaseCityKey(key)}
                            className={`px-3 py-1.5 text-sm rounded-md font-medium transition-all ${
                                baseCityKey === key 
                                ? 'bg-white text-slate-800 shadow-sm ring-1 ring-slate-200' 
                                : 'text-slate-500 hover:text-slate-700'
                            }`}
                        >
                            {CITY_DATA[key].name}
                        </button>
                    ))}
                 </div>
             </div>

             <div className="h-8 w-px bg-slate-300 mx-2"></div>

             {/* Overlay City Selector */}
             <div className="flex flex-col">
                 <label className="text-[10px] uppercase font-bold text-slate-400 tracking-wider mb-1">Overlay (Movable)</label>
                 <div className="flex gap-1">
                    {Object.keys(CITY_DATA).map(key => {
                        const isSelected = overlayCityKey === key;
                        const city = CITY_DATA[key];
                        return (
                        <button
                            key={key}
                            onClick={() => setOverlayCityKey(key === overlayCityKey ? null : key)}
                            className={`px-3 py-1.5 text-sm rounded-md font-medium transition-all flex items-center gap-2 ${
                                isSelected 
                                ? 'bg-blue-600 text-white shadow-md transform scale-105' 
                                : 'text-slate-500 hover:bg-white hover:shadow-sm'
                            }`}
                        >
                            {isSelected && <div className="w-2 h-2 rounded-full bg-white animate-pulse" />}
                            {city.name}
                        </button>
                    )})}
                 </div>
             </div>
         </div>
      </div>

      <div className="flex-1 relative">
         {/* Loading State */}
         {!isLeafletLoaded && (
            <div className="absolute inset-0 flex items-center justify-center bg-white z-50">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
            </div>
         )}
         
         <div ref={mapContainerRef} className="w-full h-full z-0 outline-none" />

         {/* Legend / Tip */}
         <div className="absolute bottom-6 left-6 pointer-events-none z-[1000]">
            <div className="bg-white/90 backdrop-blur border border-slate-200 p-4 rounded-xl shadow-xl max-w-xs pointer-events-auto">
                <div className="flex items-start gap-3 mb-3">
                    <InfoIcon />
                    <p className="text-sm text-slate-600">
                        Drag the <strong>colored marker</strong> to move the overlay city. 
                        Notice how the shape changes size as you move North/South to reflect true physical scale.
                    </p>
                </div>
                
                <div className="space-y-2 border-t pt-3">
                    <div className="flex items-center gap-2 text-xs">
                        <div className="w-6 h-4 border-2 border-dashed border-slate-400 bg-slate-400/20 rounded-sm"></div>
                        <span className="font-semibold text-slate-700">{CITY_DATA[baseCityKey].name}</span>
                        <span className="text-slate-400 ml-auto">{CITY_DATA[baseCityKey].description}</span>
                    </div>
                    
                    {overlayCityKey ? (
                        <div className="flex items-center gap-2 text-xs">
                             <div className="w-6 h-4 rounded-sm" style={{ background: CITY_DATA[overlayCityKey].color, opacity: 0.5 }}></div>
                             <span className="font-semibold text-slate-700">{CITY_DATA[overlayCityKey].name}</span>
                             <span className="text-slate-400 ml-auto">{CITY_DATA[overlayCityKey].description}</span>
                        </div>
                    ) : (
                        <div className="text-xs text-slate-400 italic pl-1">Select an overlay above to compare</div>
                    )}
                </div>
            </div>
         </div>
      </div>
    </div>
  );
}