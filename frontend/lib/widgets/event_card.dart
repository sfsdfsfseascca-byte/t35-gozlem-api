import 'package:flutter/material.dart';
import '../models/eclipse_event.dart';

class EventCard extends StatelessWidget {
  final EclipseEvent event;
  const EventCard({super.key, required this.event});

  Color _qualityColor() {
    switch (event.quality) {
      case 'excellent':
        return const Color(0xFF00E676);
      case 'good':
        return const Color(0xFFFFD600);
      default:
        return const Color(0xFFB0BEC5);
    }
  }

  IconData _kindIcon() => event.isPrimary ? Icons.brightness_3 : Icons.brightness_2;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: _qualityColor().withOpacity(0.15),
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Icon(_kindIcon(), color: _qualityColor(), size: 20),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        event.starName,
                        style: Theme.of(context).textTheme.titleLarge?.copyWith(fontSize: 18),
                      ),
                      if (event.gcvsName != null && event.gcvsName != event.starName)
                        Text(
                          event.gcvsName!,
                          style: Theme.of(context).textTheme.bodySmall?.copyWith(
                                color: const Color(0xFF8B93B8),
                              ),
                        ),
                    ],
                  ),
                ),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: _qualityColor().withOpacity(0.2),
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: Text(
                    '${event.score.toStringAsFixed(0)} • ${event.quality}',
                    style: TextStyle(
                      color: _qualityColor(),
                      fontWeight: FontWeight.w600,
                      fontSize: 12,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 14),
            _row(context, Icons.access_time, 'Local Time of Minimum',
                '${event.timeLocal} (${event.timeLocalDate}) • ${event.kind} • N=${event.cycleNumber ?? "?"}'),
            _row(context, Icons.star, 'V Magnitude',
                'V=${event.displayMagnitude}  max ${event.vMax?.toStringAsFixed(2) ?? "—"} → min ${event.vMin?.toStringAsFixed(2) ?? "—"}  depth ${event.displayDepth}'),
            _row(context, Icons.terrain, 'Peak Altitude',
                '${event.peakAltitudeDeg.toStringAsFixed(1)}° at ${event.peakAltitudeTimeLocal ?? event.timeLocal}  (now ${event.altitudeDeg.toStringAsFixed(1)}° az ${event.azimuthDeg.toStringAsFixed(0)}°)'),
            _row(context, Icons.nightlight_round, 'Moon Separation',
                '${event.moonSeparationDeg.toStringAsFixed(1)}°  •  Moon ${event.moonAltitudeDeg?.toStringAsFixed(0) ?? "—"}° alt  •  ${event.moonIlluminationPercent?.toStringAsFixed(0) ?? "—"}% illum'),
            if (event.variabilityType != null || event.spectralType != null)
              _row(context, Icons.info_outline, 'Type',
                  '${event.variabilityType ?? "—"}  ${event.spectralType ?? ""}  P=${event.periodDays.toStringAsFixed(4)}d  dur≈${event.durationEstimateH?.toStringAsFixed(1) ?? "—"}h'),
            if (event.timeUncertaintyMin > 5)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Row(
                  children: [
                    const Icon(Icons.warning_amber_rounded, size: 16, color: Colors.orange),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        'Timing uncertain by ~${event.timeUncertaintyMin.toStringAsFixed(0)} min (old epoch)',
                        style: const TextStyle(color: Colors.orange, fontSize: 12),
                      ),
                    ),
                  ],
                ),
              ),
            if (event.catalog.warnings.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  event.catalog.warnings.join(' • '),
                  style: const TextStyle(color: Color(0xFF8B93B8), fontSize: 11),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _row(BuildContext ctx, IconData icon, String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 16, color: const Color(0xFF8B93B8)),
          const SizedBox(width: 8),
          SizedBox(
            width: 150,
            child: Text(label, style: const TextStyle(color: Color(0xFF8B93B8), fontSize: 12)),
          ),
          Expanded(
            child: Text(value, style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w500)),
          ),
        ],
      ),
    );
  }
}
