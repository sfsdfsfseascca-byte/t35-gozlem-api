/// Mirrors backend/app/models.py EclipseEvent
class EclipseEvent {
  final String starName;
  final String constellation;
  final String? gcvsName;
  final String kind; // primary | secondary
  final int? cycleNumber;
  final double periodDays;
  final double epochHjd;
  final double timeJdGeocentric;
  final DateTime timeUtc;
  final String timeLocal; // HH:MM
  final DateTime timeLocalIso;
  final String timeLocalDate;
  final double timeUncertaintyMin;
  final double? vMagnitude;
  final double? vMax;
  final double? vMin;
  final double? depthMag;
  final String? variabilityType;
  final String? spectralType;
  final double? durationEstimateH;
  final double? raDeg;
  final double? decDeg;
  final double altitudeDeg;
  final double azimuthDeg;
  final double peakAltitudeDeg;
  final String? peakAltitudeTimeLocal;
  final double moonSeparationDeg;
  final double? moonAltitudeDeg;
  final double? moonIlluminationPercent;
  final double? sunAltitudeDeg;
  final bool isDark;
  final double score;
  final String quality; // excellent | good | fair
  final CatalogInfo catalog;

  EclipseEvent({
    required this.starName,
    required this.constellation,
    this.gcvsName,
    required this.kind,
    this.cycleNumber,
    required this.periodDays,
    required this.epochHjd,
    required this.timeJdGeocentric,
    required this.timeUtc,
    required this.timeLocal,
    required this.timeLocalIso,
    required this.timeLocalDate,
    required this.timeUncertaintyMin,
    this.vMagnitude,
    this.vMax,
    this.vMin,
    this.depthMag,
    this.variabilityType,
    this.spectralType,
    this.durationEstimateH,
    this.raDeg,
    this.decDeg,
    required this.altitudeDeg,
    required this.azimuthDeg,
    required this.peakAltitudeDeg,
    this.peakAltitudeTimeLocal,
    required this.moonSeparationDeg,
    this.moonAltitudeDeg,
    this.moonIlluminationPercent,
    this.sunAltitudeDeg,
    required this.isDark,
    required this.score,
    required this.quality,
    required this.catalog,
  });

  factory EclipseEvent.fromJson(Map<String, dynamic> j) {
    return EclipseEvent(
      starName: j['star_name'] as String,
      constellation: j['constellation'] as String,
      gcvsName: j['gcvs_name'] as String?,
      kind: j['kind'] as String,
      cycleNumber: j['cycle_number'] as int?,
      periodDays: (j['period_days'] as num).toDouble(),
      epochHjd: (j['epoch_hjd'] as num).toDouble(),
      timeJdGeocentric: (j['time_jd_geocentric'] as num).toDouble(),
      timeUtc: DateTime.parse(j['time_utc'] as String),
      timeLocal: j['time_local'] as String,
      timeLocalIso: DateTime.parse(j['time_local_iso'] as String),
      timeLocalDate: j['time_local_date'] as String,
      timeUncertaintyMin: (j['time_uncertainty_min'] as num).toDouble(),
      vMagnitude: (j['v_magnitude'] as num?)?.toDouble(),
      vMax: (j['v_max'] as num?)?.toDouble(),
      vMin: (j['v_min'] as num?)?.toDouble(),
      depthMag: (j['depth_mag'] as num?)?.toDouble(),
      variabilityType: j['variability_type'] as String?,
      spectralType: j['spectral_type'] as String?,
      durationEstimateH: (j['duration_estimate_h'] as num?)?.toDouble(),
      raDeg: (j['ra_deg'] as num?)?.toDouble(),
      decDeg: (j['dec_deg'] as num?)?.toDouble(),
      altitudeDeg: (j['altitude_deg'] as num).toDouble(),
      azimuthDeg: (j['azimuth_deg'] as num).toDouble(),
      peakAltitudeDeg: (j['peak_altitude_deg'] as num).toDouble(),
      peakAltitudeTimeLocal: j['peak_altitude_time_local'] as String?,
      moonSeparationDeg: (j['moon_separation_deg'] as num).toDouble(),
      moonAltitudeDeg: (j['moon_altitude_deg'] as num?)?.toDouble(),
      moonIlluminationPercent: (j['moon_illumination_percent'] as num?)?.toDouble(),
      sunAltitudeDeg: (j['sun_altitude_deg'] as num?)?.toDouble(),
      isDark: j['is_dark'] as bool,
      score: (j['score'] as num).toDouble(),
      quality: j['quality'] as String,
      catalog: CatalogInfo.fromJson(j['catalog'] as Map<String, dynamic>),
    );
  }

  bool get isPrimary => kind == 'primary';
  String get displayMagnitude => vMagnitude != null ? vMagnitude!.toStringAsFixed(2) : '—';
  String get displayDepth => depthMag != null ? '${depthMag!.toStringAsFixed(2)} mag' : '—';
}

class CatalogInfo {
  final double? raDeg;
  final double? decDeg;
  final String source; // simbad | catalog | none
  final String? simbadMainId;
  final String? simbadObjectType;
  final String? simbadSpectralType;
  final double? simbadVMag;
  final double? vMag;
  final double? vMax;
  final double? vMin;
  final double? depthMag;
  final double? crossmatchArcsec;
  final List<String> warnings;

  CatalogInfo({
    this.raDeg,
    this.decDeg,
    required this.source,
    this.simbadMainId,
    this.simbadObjectType,
    this.simbadSpectralType,
    this.simbadVMag,
    this.vMag,
    this.vMax,
    this.vMin,
    this.depthMag,
    this.crossmatchArcsec,
    required this.warnings,
  });

  factory CatalogInfo.fromJson(Map<String, dynamic> j) => CatalogInfo(
        raDeg: (j['ra_deg'] as num?)?.toDouble(),
        decDeg: (j['dec_deg'] as num?)?.toDouble(),
        source: j['source'] as String,
        simbadMainId: j['simbad_main_id'] as String?,
        simbadObjectType: j['simbad_object_type'] as String?,
        simbadSpectralType: j['simbad_spectral_type'] as String?,
        simbadVMag: (j['simbad_v_mag'] as num?)?.toDouble(),
        vMag: (j['v_mag'] as num?)?.toDouble(),
        vMax: (j['v_max'] as num?)?.toDouble(),
        vMin: (j['v_min'] as num?)?.toDouble(),
        depthMag: (j['depth_mag'] as num?)?.toDouble(),
        crossmatchArcsec: (j['crossmatch_arcsec'] as num?)?.toDouble(),
        warnings: (j['warnings'] as List<dynamic>? ?? []).cast<String>(),
      );
}

class NightResponse {
  final String queryDate;
  final DateTime generatedAt;
  final LocationInfo location;
  final WindowInfo window;
  final MoonInfo moon;
  final Map<String, dynamic> filtersApplied;
  final List<EclipseEvent> events;
  final FilterStats stats;
  final List<String> warnings;

  NightResponse({
    required this.queryDate,
    required this.generatedAt,
    required this.location,
    required this.window,
    required this.moon,
    required this.filtersApplied,
    required this.events,
    required this.stats,
    required this.warnings,
  });

  factory NightResponse.fromJson(Map<String, dynamic> j) => NightResponse(
        queryDate: j['query_date'] as String,
        generatedAt: DateTime.parse(j['generated_at'] as String),
        location: LocationInfo.fromJson(j['location'] as Map<String, dynamic>),
        window: WindowInfo.fromJson(j['window'] as Map<String, dynamic>),
        moon: MoonInfo.fromJson(j['moon'] as Map<String, dynamic>),
        filtersApplied: j['filters_applied'] as Map<String, dynamic>,
        events: (j['events'] as List).map((e) => EclipseEvent.fromJson(e as Map<String, dynamic>)).toList(),
        stats: FilterStats.fromJson(j['stats'] as Map<String, dynamic>),
        warnings: (j['warnings'] as List<dynamic>).cast<String>(),
      );
}

class LocationInfo {
  final double latitude;
  final double longitude;
  final double elevationM;
  final String? timezone;
  final String? label;
  LocationInfo({required this.latitude, required this.longitude, required this.elevationM, this.timezone, this.label});
  factory LocationInfo.fromJson(Map<String, dynamic> j) => LocationInfo(
        latitude: (j['latitude'] as num).toDouble(),
        longitude: (j['longitude'] as num).toDouble(),
        elevationM: (j['elevation_m'] as num).toDouble(),
        timezone: j['timezone'] as String?,
        label: j['label'] as String?,
      );
}

class WindowInfo {
  final DateTime startUtc;
  final DateTime endUtc;
  final String localStart;
  final String localEnd;
  final String timezone;
  final double utcOffsetHours;
  final DateTime? sunsetUtc;
  final DateTime? sunriseUtc;
  WindowInfo({
    required this.startUtc,
    required this.endUtc,
    required this.localStart,
    required this.localEnd,
    required this.timezone,
    required this.utcOffsetHours,
    this.sunsetUtc,
    this.sunriseUtc,
  });
  factory WindowInfo.fromJson(Map<String, dynamic> j) => WindowInfo(
        startUtc: DateTime.parse(j['start_utc'] as String),
        endUtc: DateTime.parse(j['end_utc'] as String),
        localStart: j['local_start'] as String,
        localEnd: j['local_end'] as String,
        timezone: j['timezone'] as String,
        utcOffsetHours: (j['utc_offset_hours'] as num).toDouble(),
        sunsetUtc: j['sunset_utc'] != null ? DateTime.parse(j['sunset_utc'] as String) : null,
        sunriseUtc: j['sunrise_utc'] != null ? DateTime.parse(j['sunrise_utc'] as String) : null,
      );
}

class MoonInfo {
  final double illuminationPercent;
  final String phaseName;
  final double elongationDeg;
  final double? altitudeDeg;
  MoonInfo({required this.illuminationPercent, required this.phaseName, required this.elongationDeg, this.altitudeDeg});
  factory MoonInfo.fromJson(Map<String, dynamic> j) => MoonInfo(
        illuminationPercent: (j['illumination_percent'] as num).toDouble(),
        phaseName: j['phase_name'] as String,
        elongationDeg: (j['elongation_deg'] as num).toDouble(),
        altitudeDeg: (j['altitude_deg'] as num?)?.toDouble(),
      );
}

class FilterStats {
  final int candidatesFromEphemeris;
  final int returned;
  final bool truncated;
  final int simbadLookups;
  final int elapsedMs;
  FilterStats({
    required this.candidatesFromEphemeris,
    required this.returned,
    required this.truncated,
    required this.simbadLookups,
    required this.elapsedMs,
  });
  factory FilterStats.fromJson(Map<String, dynamic> j) => FilterStats(
        candidatesFromEphemeris: j['candidates_from_ephemeris'] as int,
        returned: j['returned'] as int,
        truncated: j['truncated'] as bool,
        simbadLookups: j['simbad_lookups'] as int,
        elapsedMs: j['elapsed_ms'] as int,
      );
}
