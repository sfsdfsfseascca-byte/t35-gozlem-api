import 'package:flutter/material.dart';
import 'theme/app_theme.dart';
import 'screens/home_screen.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const EclipseHunterApp());
}

class EclipseHunterApp extends StatelessWidget {
  const EclipseHunterApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Eclipse Hunter',
      theme: AppTheme.dark(),
      debugShowCheckedModeBanner: false,
      home: const HomeScreen(),
    );
  }
}
