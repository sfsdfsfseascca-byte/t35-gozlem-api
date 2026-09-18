import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../services/api_service.dart';
import '../services/location_service.dart';
import '../widgets/filter_sheet.dart';
import 'results_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _api = ApiService();
  final _locService = LocationService();

  DateTime _selectedDate = DateTime.now();
  SavedLocation _location = LocationService.defaultLocation;
  FilterValues _filters = FilterValues();
  bool _loadingLocation = false;
  String? _healthStatus;

  @override
  void initState() {
    super.initState();
    _loadSavedLocation();
    _checkHealth();
  }

  Future<void> _loadSavedLocation() async {
    final loc = await _locService.getSavedOrDefault();
    setState(() => _location = loc);
  }

  Future<void> _checkHealth() async {
    try {
      final h = await _api.fetchHealth();
      setState(() => _healthStatus = '${h['ephemeris_stars']} stars • cache ${h['cache']['backend']}');
    } catch (_) {
      setState(() => _healthStatus = 'Backend unreachable at ${ApiConfig.baseUrl}');
    }
  }

  Future<void> _pickDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _selectedDate,
      firstDate: DateTime.now().subtract(const Duration(days: 1)),
      lastDate: DateTime.now().add(const Duration(days: 365 * 3)),
    );
    if (picked != null) setState(() => _selectedDate = picked);
  }

  Future<void> _useGps() async {
    setState(() => _loadingLocation = true);
    final loc = await _locService.getCurrentLocation();
    setState(() => _loadingLocation = false);
    if (loc != null) {
      setState(() => _location = loc);
      await _locService.saveLocation(loc);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Location: ${loc.latitude.toStringAsFixed(4)}, ${loc.longitude.toStringAsFixed(4)}')),
        );
      }
    } else {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Could not get GPS location. Check permissions.')),
        );
      }
    }
  }

  Future<void> _editLocation() async {
    final latCtrl = TextEditingController(text: _location.latitude.toString());
    final lonCtrl = TextEditingController(text: _location.longitude.toString());
    final elevCtrl = TextEditingController(text: _location.elevationM.toString());
    final labelCtrl = TextEditingController(text: _location.label);
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Edit Location'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(controller: labelCtrl, decoration: const InputDecoration(labelText: 'Label')),
            TextField(controller: latCtrl, decoration: const InputDecoration(labelText: 'Latitude'), keyboardType: TextInputType.number),
            TextField(controller: lonCtrl, decoration: const InputDecoration(labelText: 'Longitude'), keyboardType: TextInputType.number),
            TextField(controller: elevCtrl, decoration: const InputDecoration(labelText: 'Elevation m'), keyboardType: TextInputType.number),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Save')),
        ],
      ),
    );
    if (ok == true) {
      try {
        final newLoc = SavedLocation(
          latitude: double.parse(latCtrl.text),
          longitude: double.parse(lonCtrl.text),
          elevationM: double.tryParse(elevCtrl.text) ?? 0,
          label: labelCtrl.text,
        );
        setState(() => _location = newLoc);
        await _locService.saveLocation(newLoc);
      } catch (e) {
        if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Invalid input: $e')));
      }
    }
  }

  Future<void> _openFilters() async {
    final result = await showModalBottomSheet<FilterValues>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => FilterSheet(initial: _filters),
    );
    if (result != null) setState(() => _filters = result);
  }

  void _search() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => ResultsScreen(
          date: _selectedDate,
          location: _location,
          filters: _filters,
          api: _api,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final df = DateFormat('EEEE, MMM d, yyyy');
    return Scaffold(
      body: CustomScrollView(
        slivers: [
          SliverAppBar.large(
            title: const Text('Eclipse Hunter'),
            centerTitle: true,
            actions: [
              IconButton(icon: const Icon(Icons.tune), onPressed: _openFilters),
            ],
          ),
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (_healthStatus != null)
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                      decoration: BoxDecoration(
                        color: Colors.white.withOpacity(0.08),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Row(
                        children: [
                          const Icon(Icons.cloud_done, size: 16, color: Color(0xFF8B93B8)),
                          const SizedBox(width: 8),
                          Expanded(child: Text(_healthStatus!, style: const TextStyle(fontSize: 11, color: Color(0xFF8B93B8)))),
                        ],
                      ),
                    ),
                  const SizedBox(height: 20),
                  Text('Observing Night', style: Theme.of(context).textTheme.titleLarge),
                  const SizedBox(height: 8),
                  InkWell(
                    onTap: _pickDate,
                    borderRadius: BorderRadius.circular(12),
                    child: Container(
                      padding: const EdgeInsets.all(16),
                      decoration: BoxDecoration(
                        color: const Color(0xFF171D3A),
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Row(
                        children: [
                          const Icon(Icons.calendar_today, color: Color(0xFF6C5CE7)),
                          const SizedBox(width: 12),
                          Text(df.format(_selectedDate), style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
                          const Spacer(),
                          const Icon(Icons.chevron_right, color: Color(0xFF8B93B8)),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 24),
                  Text('Location', style: Theme.of(context).textTheme.titleLarge),
                  const SizedBox(height: 8),
                  Container(
                    padding: const EdgeInsets.all(16),
                    decoration: BoxDecoration(
                      color: const Color(0xFF171D3A),
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Column(
                      children: [
                        Row(
                          children: [
                            const Icon(Icons.place, color: Color(0xFF00CEC9)),
                            const SizedBox(width: 12),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(_location.label.isEmpty ? 'Custom' : _location.label,
                                      style: const TextStyle(fontWeight: FontWeight.w600)),
                                  Text(
                                    _locService.formatLatLon(_location.latitude, _location.longitude) +
                                        ' • ${ _location.elevationM.toStringAsFixed(0)}m',
                                    style: const TextStyle(color: Color(0xFF8B93B8), fontSize: 12),
                                  ),
                                ],
                              ),
                            ),
                            IconButton(icon: const Icon(Icons.edit, size: 20), onPressed: _editLocation),
                          ],
                        ),
                        const SizedBox(height: 12),
                        Row(
                          children: [
                            Expanded(
                              child: OutlinedButton.icon(
                                onPressed: _loadingLocation ? null : _useGps,
                                icon: _loadingLocation
                                    ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                                    : const Icon(Icons.my_location, size: 18),
                                label: const Text('Use GPS'),
                              ),
                            ),
                            const SizedBox(width: 12),
                            Expanded(
                              child: OutlinedButton.icon(
                                onPressed: _editLocation,
                                icon: const Icon(Icons.edit_location_alt, size: 18),
                                label: const Text('Edit'),
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 24),
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: Colors.white.withOpacity(0.06),
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text('Active Filters', style: TextStyle(color: Color(0xFF8B93B8), fontSize: 12)),
                        const SizedBox(height: 6),
                        Wrap(
                          spacing: 8,
                          runSpacing: 6,
                          children: [
                            _chip('Alt ≥${_filters.minAltitude.toStringAsFixed(0)}°'),
                            _chip('Moon ≥${_filters.minMoonSep.toStringAsFixed(0)}°'),
                            _chip('V ≤${_filters.maxVmag.toStringAsFixed(1)}'),
                            _chip('Depth ≥${_filters.minDepth.toStringAsFixed(1)}'),
                            _chip(_filters.includeSecondary ? 'Pri+Sec' : 'Pri only'),
                            _chip(_filters.sortBy),
                          ],
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 32),
                  SizedBox(
                    width: double.infinity,
                    height: 56,
                    child: FilledButton.icon(
                      onPressed: _search,
                      icon: const Icon(Icons.search),
                      label: const Text('Find Eclipses Tonight', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
                      style: FilledButton.styleFrom(
                        backgroundColor: const Color(0xFF6C5CE7),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  const Text(
                    'Ephemeris: Kreiner (2004) TIDAK, Univ. of Krakow. Times are geocentric, corrected for light-time. Altitudes computed with Astropy for your location.',
                    style: TextStyle(color: Color(0xFF6B7394), fontSize: 11),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _chip(String label) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: const Color(0xFF6C5CE7).withOpacity(0.2),
          borderRadius: BorderRadius.circular(20),
        ),
        child: Text(label, style: const TextStyle(fontSize: 11, color: Color(0xFFB8BED9))),
      );
}
