package com.example;

public record Album(String id, String name, String artist, String coverArt) {
    @Override public String toString() {
        return name + (artist == null || artist.isBlank() ? "" : "  —  " + artist);
    }
}