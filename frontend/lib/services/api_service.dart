import 'dart:convert';
import 'package:http/http.dart' as http;
import '../models/eclipse_event.dart';

/// Compile-time backend URL, overridable with:
/// flutter build apk --dart-define=API_BASE_URL=https://your-api.fly.dev
class ApiConfig {
  static const String baseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://10.0.2.2:8000', // Android emulator -> host localhost
  );
  static const String apiKey = String.fromEnvironment('API_KEY', defaultValue: '');
}

class ApiService {
  final http.Client _client;
  final String baseUrl;

  ApiService({http.Client? client, String? baseUrl})
      : _client = client ?? http.Client(),
        baseUrl = (baseUrl ?? ApiConfig.baseUrl).replaceAll(RegExp(r'/$'), '');

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (ApiConfig.apiKey.isNotEmpty) 'X-API-Key': ApiConfig.apiKey,
      };

  Future<NightResponse> fetchNight({
    required DateTime date,
    required double latitude,
    required double longitude,
    double elevationM = 0,
    String? timezone,
    double minAltitude = 30,
    double minMoonSep = 30,
    double maxVmag = 9.5,
    double minDepth = 0.3,
    double maxSunAlt = -6,
    bool includeSecondary = true,
    bool requireDarkSky = true,
    bool useSimbad = true,
    int maxResults = 150,
    String sortBy = 'time',
  }) async {
    final body = {
      'date': date.toIso8601String().split('T').first,
      'location': {
        'latitude': latitude,
        'longitude': longitude,
        'elevation_m': elevationM,
        if (timezone != null) 'timezone': timezone,
      },
      'min_altitude_deg': minAltitude,
      'min_moon_separation_deg': minMoonSep,
      'max_vmag': maxVmag,
      'min_depth_mag': minDepth,
      'max_sun_altitude_deg': maxSunAlt,
      'include_secondary': includeSecondary,
      'require_dark_sky': requireDarkSky,
      'use_simbad': useSimbad,
      'max_results': maxResults,
      'sort_by': sortBy,
    };

    final uri = Uri.parse('$baseUrl/api/v1/night');
    final resp = await _client
        .post(uri, headers: _headers, body: jsonEncode(body))
        .timeout(const Duration(seconds: 30));

    if (resp.statusCode != 200) {
      throw ApiException(resp.statusCode, resp.body);
    }
    final json = jsonDecode(resp.body) as Map<String, dynamic>;
    return NightResponse.fromJson(json);
  }

  Future<Map<String, dynamic>> fetchDefaults() async {
    final uri = Uri.parse('$baseUrl/api/v1/defaults');
    final resp = await _client.get(uri, headers: _headers).timeout(const Duration(seconds: 10));
    if (resp.statusCode != 200) throw ApiException(resp.statusCode, resp.body);
    return jsonDecode(resp.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> fetchHealth() async {
    final uri = Uri.parse('$baseUrl/health');
    final resp = await _client.get(uri, headers: _headers).timeout(const Duration(seconds: 10));
    if (resp.statusCode != 200) throw ApiException(resp.statusCode, resp.body);
    return jsonDecode(resp.body) as Map<String, dynamic>;
  }
}

class ApiException implements Exception {
  final int statusCode;
  final String body;
  ApiException(this.statusCode, this.body);
  @override
  String toString() => 'ApiException $statusCode: $body';
}
