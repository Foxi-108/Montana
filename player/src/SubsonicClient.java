package com.example;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

public class SubsonicClient {
    private final String baseUrl;
    private final String user;
    private final String password;
    private final HttpClient http = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(10)).build();
    private final ObjectMapper json = new ObjectMapper();
    private static final SecureRandom RNG = new SecureRandom();

    public SubsonicClient(String baseUrl, String user, String password) {
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.user = user;
        this.password = password;
    }

    private String authQuery() {
        String salt = randomHex(8);
        String token = md5(password + salt);
        return "u=" + enc(user) + "&t=" + token + "&s=" + salt
                + "&v=1.16.1&c=MyPlayer&f=json";
    }

    /** Общий вызов REST API. Бросает исключение, если status != ok */
    public JsonNode call(String method, Map<String, String> params) throws Exception {
        StringBuilder q = new StringBuilder(authQuery());
        for (var e : params.entrySet()) {
            q.append("&").append(enc(e.getKey())).append("=").append(enc(e.getValue()));
        }
        URI uri = URI.create(baseUrl + "/rest/" + method + ".view?" + q);
        HttpRequest req = HttpRequest.newBuilder(uri)
                .timeout(Duration.ofSeconds(20)).GET().build();
        HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
        if (resp.statusCode() != 200) {
            throw new RuntimeException("HTTP " + resp.statusCode() + ": " + resp.body());
        }
        JsonNode root = json.readTree(resp.body()).path("subsonic-response");
        if (!"ok".equals(root.path("status").asText())) {
            JsonNode err = root.path("error");
            throw new RuntimeException("Subsonic error " + err.path("code").asText()
                    + ": " + err.path("message").asText());
        }
        return root;
    }

    public boolean ping() throws Exception {
        call("ping", Map.of());
        return true;
    }

    public List<Artist> getArtists() throws Exception {
        JsonNode artists = call("getArtists", Map.of()).path("artists");
        List<Artist> out = new ArrayList<>();
        for (JsonNode index : artists.path("index")) {
            for (JsonNode a : index.path("artist")) {
                out.add(new Artist(a.path("id").asText(), a.path("name").asText()));
            }
        }
        return out;
    }

    public List<Album> getArtistAlbums(String artistId) throws Exception {
        JsonNode artist = call("getArtist", Map.of("id", artistId)).path("artist");
        List<Album> out = new ArrayList<>();
        for (JsonNode a : artist.path("album")) {
            out.add(new Album(
                    a.path("id").asText(),
                    a.path("name").asText(),
                    a.path("artist").asText(),
                    a.path("coverArt").asText()
            ));
        }
        return out;
    }

    public List<Song> getAlbumSongs(String albumId) throws Exception {
        JsonNode album = call("getAlbum", Map.of("id", albumId)).path("album");
        List<Song> out = new ArrayList<>();
        for (JsonNode s : album.path("song")) {
            out.add(new Song(
                    s.path("id").asText(),
                    s.path("title").asText(),
                    s.path("album").asText(),
                    s.path("artist").asText(),
                    s.path("duration").asInt(),
                    s.path("track").asInt()
            ));
        }
        return out;
    }

    /** Прямой URL аудиопотока — можно скормить MediaPlayer'у */
    public String streamUrl(String songId) {
        return baseUrl + "/rest/stream.view?" + authQuery() + "&id=" + enc(songId);
    }

    /** URL обложки — тоже рабочий (можно грузить в Image) */
    public String coverArtUrl(String id) {
        return baseUrl + "/rest/getCoverArt.view?" + authQuery() + "&id=" + enc(id);
    }

    /** submission=false -> "now playing", true -> "прослушано" */
    public void scrobble(String songId, boolean submission) {
        try {
            String q = authQuery() + "&id=" + enc(songId) + "&submission=" + submission;
            HttpRequest req = HttpRequest.newBuilder(
                    URI.create(baseUrl + "/rest/scrobble.view?" + q))
                    .timeout(Duration.ofSeconds(10)).GET().build();
            http.sendAsync(req, HttpResponse.BodyHandlers.discarding());
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    private static String md5(String s) {
        try {
            byte[] d = MessageDigest.getInstance("MD5").digest(s.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : d) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Exception e) { throw new RuntimeException(e); }
    }

    private static String enc(String s) {
        return URLEncoder.encode(s, StandardCharsets.UTF_8);
    }

    private static String randomHex(int n) {
        byte[] b = new byte[n];
        RNG.nextBytes(b);
        StringBuilder sb = new StringBuilder();
        for (byte x : b) sb.append(String.format("%02x", x));
        return sb.toString();
    }
}