package com.example;

import javafx.application.Application;
import javafx.application.Platform;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Scene;
import javafx.scene.control.*;
import javafx.scene.layout.*;
import javafx.scene.media.Media;
import javafx.scene.media.MediaPlayer;
import javafx.stage.Stage;
import javafx.util.Duration;

import java.util.concurrent.CompletableFuture;

public class App extends Application {
    private SubsonicClient client;
    private MediaPlayer mediaPlayer;

    private ListView<Artist> artistList;
    private ListView<Album>  albumList;
    private ListView<Song>   songList;
    private Label            nowPlayingLabel;
    private Slider           progressSlider;
    private Label            timeLabel;
    private Slider           volumeSlider;
    private Button           playPauseButton;

    private boolean userSeeking = false;
    private Song    currentSong;
    private boolean scrobbleSent = false;

    @Override
    public void start(Stage stage) {
        if (!showLoginDialog()) { Platform.exit(); return; }
        stage.setTitle("Subsonic Player");
        stage.setScene(new Scene(buildMainView(), 1100, 700));
        stage.show();
        loadArtists();
    }

    // ---------------- Логин ----------------
    private boolean showLoginDialog() {
        Dialog<Boolean> dialog = new Dialog<>();
        dialog.setTitle("Connect to Navidrome / Subsonic");

        TextField urlField  = new TextField("http://localhost:4533");
        TextField userField = new TextField();
        PasswordField passField = new PasswordField();
        Label status = new Label();

        GridPane grid = new GridPane();
        grid.setHgap(8); grid.setVgap(8);
        grid.setPadding(new Insets(15));
        grid.add(new Label("Server URL:"), 0, 0); grid.add(urlField, 1, 0);
        grid.add(new Label("Username:"),   0, 1); grid.add(userField, 1, 1);
        grid.add(new Label("Password:"),   0, 2); grid.add(passField, 1, 2);
        grid.add(status, 0, 3, 2, 1);

        ButtonType connectType = new ButtonType("Connect", ButtonBar.ButtonData.OK_DONE);
        dialog.getDialogPane().getButtonTypes().addAll(connectType, ButtonType.CANCEL);
        dialog.getDialogPane().setContent(grid);

        Button connectBtn = (Button) dialog.getDialogPane().lookupButton(connectType);
        connectBtn.addEventFilter(javafx.event.ActionEvent.ACTION, e -> {
            status.setText("Connecting...");
            try {
                SubsonicClient c = new SubsonicClient(
                        urlField.getText(), userField.getText(), passField.getText());
                if (c.ping()) {
                    client = c;
                    status.setText("OK");
                } else {
                    status.setText("Failed");
                    e.consume();
                }
            } catch (Exception ex) {
                status.setText("Error: " + ex.getMessage());
                e.consume();
            }
        });

        return dialog.showAndWait().orElse(false);
    }

    // ---------------- Основной UI ----------------
    private BorderPane buildMainView() {
        artistList = new ListView<>();
        albumList  = new ListView<>();
        songList   = new ListView<>();

        artistList.getSelectionModel().selectedItemProperty().addListener(
                (obs, o, a) -> { if (a != null) loadAlbums(a); });
        albumList.getSelectionModel().selectedItemProperty().addListener(
                (obs, o, al) -> { if (al != null) loadSongs(al); });
        songList.setOnMouseClicked(e -> {
            if (e.getClickCount() == 2) {
                Song s = songList.getSelectionModel().getSelectedItem();
                if (s != null) playSong(s);
            }
        });

        VBox artistPane = titled("Artists", artistList);
        VBox albumPane  = titled("Albums",  albumList);
        VBox songPane   = titled("Songs (double-click to play)", songList);
        artistPane.setPrefWidth(250);
        albumPane.setPrefWidth(320);
        songPane.setPrefWidth(480);

        HBox lists = new HBox(5, artistPane, albumPane, songPane);
        HBox.setHgrow(songPane, Priority.ALWAYS);

        // Плеерная панель
        playPauseButton = new Button("\u25B6");
        playPauseButton.setPrefWidth(45);
        playPauseButton.setOnAction(e -> togglePlay());

        progressSlider = new Slider(0, 100, 0);
        progressSlider.setDisable(true);
        HBox.setHgrow(progressSlider, Priority.ALWAYS);
        progressSlider.setOnMousePressed(e -> userSeeking = true);
        progressSlider.setOnMouseReleased(e -> {
            if (mediaPlayer != null) {
                Duration total = mediaPlayer.getTotalDuration();
                if (total != null && total.toMillis() > 0) {
                    mediaPlayer.seek(total.multiply(progressSlider.getValue() / 100.0));
                }
            }
            userSeeking = false;
        });

        timeLabel = new Label("0:00 / 0:00");
        timeLabel.setMinWidth(110);
        timeLabel.setAlignment(Pos.CENTER);

        nowPlayingLabel = new Label("Nothing playing");
        nowPlayingLabel.setMinWidth(240);

        volumeSlider = new Slider(0, 1, 0.8);
        volumeSlider.setPrefWidth(100);
        volumeSlider.valueProperty().addListener((obs, o, n) -> {
            if (mediaPlayer != null) mediaPlayer.setVolume(n.doubleValue());
        });

        HBox playerBar = new HBox(8,
                nowPlayingLabel, playPauseButton, progressSlider, timeLabel,
                new Label("Vol:"), volumeSlider);
        playerBar.setPadding(new Insets(8));
        playerBar.setAlignment(Pos.CENTER_LEFT);
        playerBar.setStyle("-fx-background-color: #eeeeee;");

        BorderPane root = new BorderPane();
        root.setCenter(lists);
        root.setBottom(playerBar);
        return root;
    }

    private VBox titled(String title, Control content) {
        Label l = new Label(title);
        l.setStyle("-fx-font-weight: bold; -fx-padding: 4 8;");
        VBox box = new VBox(l, content);
        VBox.setVgrow(content, Priority.ALWAYS);
        return box;
    }

    // ---------------- Загрузка данных ----------------
    private void loadArtists() {
        CompletableFuture.supplyAsync(() -> {
            try { return client.getArtists(); }
            catch (Exception e) { throw new RuntimeException(e); }
        }).thenAcceptAsync(items -> artistList.getItems().setAll(items), Platform::runLater)
          .exceptionally(this::showError);
    }

    private void loadAlbums(Artist a) {
        albumList.getItems().clear();
        songList.getItems().clear();
        CompletableFuture.supplyAsync(() -> {
            try { return client.getArtistAlbums(a.id()); }
            catch (Exception e) { throw new RuntimeException(e); }
        }).thenAcceptAsync(items -> albumList.getItems().setAll(items), Platform::runLater)
          .exceptionally(this::showError);
    }

    private void loadSongs(Album al) {
        songList.getItems().clear();
        CompletableFuture.supplyAsync(() -> {
            try { return client.getAlbumSongs(al.id()); }
            catch (Exception e) { throw new RuntimeException(e); }
        }).thenAcceptAsync(items -> songList.getItems().setAll(items), Platform::runLater)
          .exceptionally(this::showError);
    }

    // ---------------- Воспроизведение ----------------
    private void playSong(Song s) {
        if (mediaPlayer != null) {
            mediaPlayer.stop();
            mediaPlayer.dispose();
            mediaPlayer = null;
        }
        try {
            String url = client.streamUrl(s.id());
            Media media = new Media(url);
            mediaPlayer = new MediaPlayer(media);
            mediaPlayer.setVolume(volumeSlider.getValue());

            currentSong = s;
            scrobbleSent = false;
            progressSlider.setDisable(false);
            progressSlider.setValue(0);
            nowPlayingLabel.setText(s.title() + "  —  " + s.artist());

            mediaPlayer.currentTimeProperty().addListener((obs, o, n) -> {
                if (mediaPlayer == null) return;
                Duration total = mediaPlayer.getTotalDuration();
                if (!userSeeking && total != null && total.toMillis() > 0) {
                    progressSlider.setValue(n.toMillis() * 100.0 / total.toMillis());
                }
                timeLabel.setText(fmt(n) + " / " + fmt(total));
            });
            mediaPlayer.setOnReady(() -> {
                playPauseButton.setText("\u23F8");
                timeLabel.setText(fmt(Duration.ZERO) + " / " + fmt(mediaPlayer.getTotalDuration()));
            });
            mediaPlayer.setOnEndOfMedia(() -> {
                playPauseButton.setText("\u25B6");
                if (!scrobbleSent && currentSong != null) {
                    client.scrobble(currentSong.id(), true);
                    scrobbleSent = true;
                }
            });
            mediaPlayer.setOnError(() -> showError(mediaPlayer.getError()));
            mediaPlayer.play();

            // "now playing" на сервере
            client.scrobble(s.id(), false);
        } catch (Exception e) {
            showError(e);
        }
    }

    private void togglePlay() {
        if (mediaPlayer == null) return;
        if (mediaPlayer.getStatus() == MediaPlayer.Status.PLAYING) {
            mediaPlayer.pause();
            playPauseButton.setText("\u25B6");
        } else {
            mediaPlayer.play();
            playPauseButton.setText("\u23F8");
        }
    }

    private static String fmt(Duration d) {
        if (d == null || d == Duration.UNKNOWN) return "0:00";
        int totalSec = (int) d.toSeconds();
        if (totalSec < 0) return "0:00";
        return String.format("%d:%02d", totalSec / 60, totalSec % 60);
    }

    private Void showError(Throwable t) {
        Throwable cause = (t instanceof java.util.concurrent.CompletionException && t.getCause() != null)
                ? t.getCause() : t;
        Platform.runLater(() -> {
            Alert a = new Alert(Alert.AlertType.ERROR,
                    cause.getClass().getSimpleName() + ": " + cause.getMessage());
            a.showAndWait();
        });
        return null;
    }

    public static void main(String[] args) { launch(args); }
}