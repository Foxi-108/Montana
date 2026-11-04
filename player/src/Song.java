package com.example;

public record Song(String id, String title, String album, String artist,
                   int duration, int track) {
    @Override public String toString() {
        String num = track > 0 ? String.format("%02d. ", track) : "";
        return num + title;
    }
}