package com.example;

public record Artist(String id, String name) {
    @Override public String toString() { return name; }
}