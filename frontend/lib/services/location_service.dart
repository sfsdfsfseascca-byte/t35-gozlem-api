import 'package:geolocator/geolocator.dart';
import 'package:shared_preferences/shared_preferences.dart';

class SavedLocation {
  final double latitude;
  final double longitude;
  final double elevationM;
  final String label;
  const SavedLocation({
    required this.latitude,
    required this.longitude,
    this.elevationM = 0,
    this.label = '',
  });

  Map<String, dynamic> toJson() => {
        'lat': latitude,
        'lon': longitude,
        'elev': elevationM,
        'label': label,
      };

  static SavedLocation? fromJson(Map<String, dynamic>? j) {
    if (j == null) return null;
    return SavedLocation(
      latitude: (j['lat'] as num).toDouble(),
      longitude: (j['lon'] as num).toDouble(),
      elevationM: (j['elev'] as num?)?.toDouble() ?? 0,
      label: j['label'] as String? ?? '',
    );
  }
}

class LocationService {
  static const _kLat = 'eh_lat';
  static const _kLon = 'eh_lon';
  static const _kLabel = 'eh_label';
  static const _kElev = 'eh_elev';

  // Kayseri default (user location from prompt)
  static const defaultLocation = SavedLocation(
    latitude: 38.7208,
    longitude: 35.4875,
    elevationM: 1050,
    label: 'Kayseri, TR',
  );

  Future<bool> _ensurePermission() async {
    final enabled = await Geolocator.isLocationServiceEnabled();
    if (!enabled) return false;
    var perm = await Geolocator.checkPermission();
    if (perm == LocationPermission.denied) {
      perm = await Geolocator.requestPermission();
    }
    return perm == LocationPermission.always || perm == LocationPermission.whileInUse;
  }

  Future<SavedLocation?> getCurrentLocation() async {
    try {
      final ok = await _ensurePermission();
      if (!ok) return null;
      final pos = await Geolocator.getCurrentPosition(
        desiredAccuracy: LocationAccuracy.medium,
        timeLimit: const Duration(seconds: 10),
      );
      return SavedLocation(
        latitude: pos.latitude,
        longitude: pos.longitude,
        elevationM: pos.altitude,
        label: 'Current location',
      );
    } catch (_) {
      return null;
    }
  }

  Future<SavedLocation> getSavedOrDefault() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final lat = prefs.getDouble(_kLat);
      final lon = prefs.getDouble(_kLon);
      if (lat != null && lon != null) {
        return SavedLocation(
          latitude: lat,
          longitude: lon,
          elevationM: prefs.getDouble(_kElev) ?? 0,
          label: prefs.getString(_kLabel) ?? 'Saved',
        );
      }
    } catch (_) {}
    return defaultLocation;
  }

  Future<void> saveLocation(SavedLocation loc) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_kLat, loc.latitude);
    await prefs.setDouble(_kLon, loc.longitude);
    await prefs.setDouble(_kElev, loc.elevationM);
    await prefs.setString(_kLabel, loc.label);
  }

  String formatLatLon(double lat, double lon) {
    final ns = lat >= 0 ? 'N' : 'S';
    final ew = lon >= 0 ? 'E' : 'W';
    return '${lat.abs().toStringAsFixed(4)}°$ns, ${lon.abs().toStringAsFixed(4)}°$ew';
  }
}
