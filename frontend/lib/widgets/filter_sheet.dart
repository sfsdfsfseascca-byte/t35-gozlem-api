import 'package:flutter/material.dart';

class FilterValues {
  double minAltitude;
  double minMoonSep;
  double maxVmag;
  double minDepth;
  bool includeSecondary;
  bool requireDarkSky;
  bool useSimbad;
  String sortBy;
  FilterValues({
    this.minAltitude = 30,
    this.minMoonSep = 30,
    this.maxVmag = 9.5,
    this.minDepth = 0.3,
    this.includeSecondary = true,
    this.requireDarkSky = true,
    this.useSimbad = true,
    this.sortBy = 'time',
  });
}

class FilterSheet extends StatefulWidget {
  final FilterValues initial;
  const FilterSheet({super.key, required this.initial});
  @override
  State<FilterSheet> createState() => _FilterSheetState();
}

class _FilterSheetState extends State<FilterSheet> {
  late FilterValues v;
  @override
  void initState() {
    super.initState();
    v = FilterValues(
      minAltitude: widget.initial.minAltitude,
      minMoonSep: widget.initial.minMoonSep,
      maxVmag: widget.initial.maxVmag,
      minDepth: widget.initial.minDepth,
      includeSecondary: widget.initial.includeSecondary,
      requireDarkSky: widget.initial.requireDarkSky,
      useSimbad: widget.initial.useSimbad,
      sortBy: widget.initial.sortBy,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 24),
      decoration: const BoxDecoration(
        color: Color(0xFF171D3A),
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Center(
              child: Container(
                width: 40,
                height: 4,
                decoration: BoxDecoration(color: Colors.white24, borderRadius: BorderRadius.circular(2)),
              ),
            ),
            const SizedBox(height: 16),
            Text('Filters', style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 16),
            _slider('Min Altitude', v.minAltitude, 0, 60, (x) => setState(() => v.minAltitude = x), '°'),
            _slider('Min Moon Separation', v.minMoonSep, 0, 90, (x) => setState(() => v.minMoonSep = x), '°'),
            _slider('Max V Magnitude', v.maxVmag, 2, 12, (x) => setState(() => v.maxVmag = x), ''),
            _slider('Min Depth', v.minDepth, 0, 2, (x) => setState(() => v.minDepth = x), ' mag'),
            const Divider(color: Colors.white12),
            _toggle('Include secondary minima', v.includeSecondary, (x) => setState(() => v.includeSecondary = x)),
            _toggle('Require dark sky (Sun < -6°)', v.requireDarkSky, (x) => setState(() => v.requireDarkSky = x)),
            _toggle('Use SIMBAD verification', v.useSimbad, (x) => setState(() => v.useSimbad = x)),
            const SizedBox(height: 8),
            const Text('Sort by', style: TextStyle(color: Color(0xFF8B93B8), fontSize: 12)),
            DropdownButton<String>(
              value: v.sortBy,
              isExpanded: true,
              dropdownColor: const Color(0xFF0B1026),
              items: const [
                DropdownMenuItem(value: 'time', child: Text('Time')),
                DropdownMenuItem(value: 'score', child: Text('Score')),
                DropdownMenuItem(value: 'altitude', child: Text('Peak Altitude')),
                DropdownMenuItem(value: 'magnitude', child: Text('Brightness')),
              ],
              onChanged: (s) => setState(() => v.sortBy = s ?? 'time'),
            ),
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: () => Navigator.pop(context, v),
                child: const Text('Apply'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _slider(String label, double value, double min, double max, ValueChanged<double> onChanged, String suffix) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(label, style: const TextStyle(color: Color(0xFFB8BED9), fontSize: 13)),
            Text('${value.toStringAsFixed(1)}$suffix', style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
          ],
        ),
        Slider(value: value, min: min, max: max, divisions: 60, onChanged: onChanged),
      ],
    );
  }

  Widget _toggle(String label, bool value, ValueChanged<bool> onChanged) {
    return SwitchListTile(
      title: Text(label, style: const TextStyle(fontSize: 13)),
      value: value,
      onChanged: onChanged,
      contentPadding: EdgeInsets.zero,
    );
  }
}
