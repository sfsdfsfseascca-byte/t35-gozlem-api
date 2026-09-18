import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

class AppTheme {
  static const _primary = Color(0xFF0B1026);
  static const _accent = Color(0xFF6C5CE7);
  static const _accent2 = Color(0xFF00CEC9);
  static const _card = Color(0xFF171D3A);
  static const _bg = Color(0xFF0A0E23);

  static ThemeData dark() {
    final base = ThemeData.dark(useMaterial3: true);
    return base.copyWith(
      scaffoldBackgroundColor: _bg,
      colorScheme: const ColorScheme.dark(
        primary: _accent,
        secondary: _accent2,
        surface: _card,
        background: _bg,
      ),
      textTheme: GoogleFonts.interTextTheme(base.textTheme).copyWith(
        displaySmall: GoogleFonts.spaceGrotesk(
          fontSize: 28, fontWeight: FontWeight.w700, color: Colors.white),
        titleLarge: GoogleFonts.spaceGrotesk(
          fontSize: 20, fontWeight: FontWeight.w600, color: Colors.white),
        bodyMedium: const TextStyle(color: Color(0xFFB8BED9)),
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: Colors.transparent,
        elevation: 0,
        centerTitle: true,
      ),
      cardTheme: CardThemeData(
        color: _card,
        elevation: 0,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: _card,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide.none,
        ),
        hintStyle: const TextStyle(color: Color(0xFF6B7394)),
      ),
    );
  }

  static const gradient = LinearGradient(
    colors: [_accent, _accent2],
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
  );
}
