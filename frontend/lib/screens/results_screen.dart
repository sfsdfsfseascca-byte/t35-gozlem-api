import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../models/eclipse_event.dart';
import '../services/api_service.dart';
import '../services/location_service.dart';
import '../widgets/event_card.dart';
import '../widgets/filter_sheet.dart';

class ResultsScreen extends StatefulWidget {
  final DateTime date;
  final SavedLocation location;
  final FilterValues filters;
  final ApiService api;
  const ResultsScreen({
    super.key,
    required this.date,
    required this.location,
    required this.filters,
    required this.api,
  });
  @override
  State<ResultsScreen> createState() => _ResultsScreenState();
}

class _ResultsScreenState extends State<ResultsScreen> {
  late Future<NightResponse> _future;
  late FilterValues _filters;

  @override
  void initState() {
    super.initState();
    _filters = widget.filters;
    _future = _fetch();
  }

  Future<NightResponse> _fetch() {
    return widget.api.fetchNight(
      date: widget.date,
      latitude: widget.location.latitude,
      longitude: widget.location.longitude,
      elevationM: widget.location.elevationM,
      timezone: 'Europe/Istanbul', // explicit for Kayseri default, but could be derived
      minAltitude: _filters.minAltitude,
      minMoonSep: _filters.minMoonSep,
      maxVmag: _filters.maxVmag,
      minDepth: _filters.minDepth,
      includeSecondary: _filters.includeSecondary,
      requireDarkSky: _filters.requireDarkSky,
      useSimbad: _filters.useSimbad,
      maxResults: 150,
      sortBy: _filters.sortBy,
    );
  }

  Future<void> _refresh() async {
    setState(() => _future = _fetch());
    await _future;
  }

  @override
  Widget build(BuildContext context) {
    final df = DateFormat('MMM d, yyyy');
    return Scaffold(
      appBar: AppBar(
        title: Text(df.format(widget.date)),
        actions: [
          IconButton(
            icon: const Icon(Icons.tune),
            onPressed: () async {
              final res = await showModalBottomSheet<FilterValues>(
                context: context,
                isScrollControlled: true,
                backgroundColor: Colors.transparent,
                builder: (_) => FilterSheet(initial: _filters),
              );
              if (res != null) {
                setState(() {
                  _filters = res;
                  _future = _fetch();
                });
              }
            },
          ),
        ],
      ),
      body: FutureBuilder<NightResponse>(
        future: _future,
        builder: (ctx, snap) {
          if (snap.connectionState == ConnectionState.waiting) {
            return const Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  CircularProgressIndicator(),
                  SizedBox(height: 16),
                  Text('Computing minima for your sky...', style: TextStyle(color: Color(0xFF8B93B8))),
                  SizedBox(height: 8),
                  Text('Vectorized AltAz + SIMBAD cache', style: TextStyle(color: Color(0xFF6B7394), fontSize: 11)),
                ],
              ),
            );
          }
          if (snap.hasError) {
            final err = snap.error!;
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(Icons.error_outline, size: 48, color: Colors.redAccent),
                    const SizedBox(height: 12),
                    Text('Failed to load', style: Theme.of(context).textTheme.titleLarge),
                    const SizedBox(height: 8),
                    Text(err.toString(), style: const TextStyle(color: Color(0xFF8B93B8), fontSize: 12)),
                    const SizedBox(height: 16),
                    FilledButton.icon(onPressed: _refresh, icon: const Icon(Icons.refresh), label: const Text('Retry')),
                    const SizedBox(height: 12),
                    Text('Backend: ${ApiConfig.baseUrl}', style: const TextStyle(color: Color(0xFF6B7394), fontSize: 10)),
                  ],
                ),
              ),
            );
          }
          final data = snap.data!;
          if (data.events.isEmpty) {
            return Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Icon(Icons.nights_stay, size: 48, color: Color(0xFF6B7394)),
                  const SizedBox(height: 12),
                  const Text('No observable eclipses tonight'),
                  const SizedBox(height: 8),
                  Text(
                    'Try lowering Min Altitude or Max Vmag, or include secondary minima.',
                    style: const TextStyle(color: Color(0xFF8B93B8), fontSize: 12),
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Moon: ${data.moon.phaseName} ${data.moon.illuminationPercent.toStringAsFixed(0)}% • '
                    '${data.stats.candidatesFromEphemeris} candidates checked',
                    style: const TextStyle(color: Color(0xFF6B7394), fontSize: 11),
                  ),
                ],
              ),
            );
          }
          return RefreshIndicator(
            onRefresh: _refresh,
            child: CustomScrollView(
              slivers: [
                SliverToBoxAdapter(
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Container(
                          padding: const EdgeInsets.all(12),
                          decoration: BoxDecoration(
                            color: const Color(0xFF171D3A),
                            borderRadius: BorderRadius.circular(12),
                          ),
                          child: Row(
                            children: [
                              const Icon(Icons.dark_mode, size: 18, color: Color(0xFF00CEC9)),
                              const SizedBox(width: 8),
                              Expanded(
                                child: Text(
                                  '${data.window.localStart} → ${data.window.localEnd}  •  '
                                  'Moon ${data.moon.phaseName} ${data.moon.illuminationPercent.toStringAsFixed(0)}%  •  '
                                  '${data.events.length} events  •  ${data.stats.elapsedMs}ms',
                                  style: const TextStyle(color: Color(0xFFB8BED9), fontSize: 12),
                                ),
                              ),
                            ],
                          ),
                        ),
                        if (data.warnings.isNotEmpty) ...[
                          const SizedBox(height: 8),
                          for (final w in data.warnings)
                            Padding(
                              padding: const EdgeInsets.only(bottom: 4),
                              child: Row(
                                children: [
                                  const Icon(Icons.info_outline, size: 14, color: Colors.orange),
                                  const SizedBox(width: 6),
                                  Expanded(child: Text(w, style: const TextStyle(color: Colors.orange, fontSize: 11))),
                                ],
                              ),
                            ),
                        ],
                      ],
                    ),
                  ),
                ),
                SliverList(
                  delegate: SliverChildBuilderDelegate(
                    (ctx, i) => EventCard(event: data.events[i]),
                    childCount: data.events.length,
                  ),
                ),
                const SliverToBoxAdapter(child: SizedBox(height: 24)),
              ],
            ),
          );
        },
      ),
    );
  }
}
