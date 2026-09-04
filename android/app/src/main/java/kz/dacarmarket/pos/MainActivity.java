package kz.dacarmarket.pos;

import android.Manifest;
import android.annotation.SuppressLint;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.ProgressBar;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

public class MainActivity extends AppCompatActivity {

    // Server URLs — Production dacar-market.kz
    private static final String SERVER_URL = "https://dacar-market.kz/m/";
    private static final String DEV_URL = "http://10.0.2.2:8000/m/";
    private static final int PERMISSION_REQUEST_CODE = 1001;
    private static final int FILE_CHOOSER_REQUEST_CODE = 1002;

    private WebView webView;
    private SwipeRefreshLayout swipeRefreshLayout;
    private ProgressBar progressBar;
    private ValueCallback<Uri[]> fileUploadCallback;
    private PermissionRequest pendingPermissionRequest;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        webView = findViewById(R.id.webView);
        swipeRefreshLayout = findViewById(R.id.swipeRefreshLayout);
        progressBar = findViewById(R.id.progressBar);

        setupWebView();
        setupSwipeRefresh();
        checkAppPermissions();

        // Load POS
        webView.loadUrl(SERVER_URL);
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void setupWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setLoadsImagesAutomatically(true);
        // Enable wide viewport so responsive layout correctly reads the viewport meta tag on tablets
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setTextZoom(100);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setMediaPlaybackRequiresUserGesture(false);

        // Custom User-Agent to identify DACAR Android Native App
        String defaultUa = settings.getUserAgentString();
        settings.setUserAgentString(defaultUa + " DACARMobileApp/1.0 DACAR_Android_Native_POS/1.0.0");

        // Enable Cookies
        CookieManager cookieManager = CookieManager.getInstance();
        cookieManager.setAcceptCookie(true);
        cookieManager.setAcceptThirdPartyCookies(webView, true);

        // Dark Background to prevent white screen flash
        webView.setBackgroundColor(android.graphics.Color.parseColor("#0B0F19"));

        // Hardware Acceleration
        webView.setLayerType(View.LAYER_TYPE_HARDWARE, null);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                if (url.startsWith("http://") || url.startsWith("https://")) {
                    return false; // Load inside WebView
                }
                try {
                    Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                    startActivity(intent);
                    return true;
                } catch (Exception e) {
                    return false;
                }
            }

            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                progressBar.setVisibility(View.VISIBLE);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                progressBar.setVisibility(View.GONE);
                swipeRefreshLayout.setRefreshing(false);
            }

            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                showErrorHtml(view, "Ошибка связи с сервером", "Не удалось загрузить: " + failingUrl);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, android.webkit.WebResourceError error) {
                super.onReceivedError(view, request, error);
                if (request.isForMainFrame()) {
                    String failingUrl = request.getUrl().toString();
                    showErrorHtml(view, "Ошибка связи с сервером", "Не удалось загрузить страницу.<br><small style='color:#64748B; word-break: break-all;'>" + failingUrl + "</small>");
                }
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request, android.webkit.WebResourceResponse errorResponse) {
                super.onReceivedHttpError(view, request, errorResponse);
                if (request.isForMainFrame() && errorResponse.getStatusCode() >= 400) {
                    String failingUrl = request.getUrl().toString();
                    showErrorHtml(view, "Ошибка сервера (" + errorResponse.getStatusCode() + ")", 
                        "Сервер вернул ошибку " + errorResponse.getStatusCode() + ".<br><small style='color:#64748B; word-break: break-all;'>" + failingUrl + "</small>");
                }
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                progressBar.setProgress(newProgress);
                if (newProgress >= 100) {
                    progressBar.setVisibility(View.GONE);
                }
            }

            // Handle Camera Permission for Barcode Scanner (Huawei EMUI / Android 10+ compatible)
            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                MainActivity.this.runOnUiThread(() -> {
                    pendingPermissionRequest = request;
                    if (ContextCompat.checkSelfPermission(MainActivity.this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
                        try {
                            request.grant(request.getResources());
                        } catch (Exception e) {
                            try {
                                request.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
                            } catch (Exception ignored) {}
                        }
                    } else {
                        ActivityCompat.requestPermissions(MainActivity.this, new String[]{Manifest.permission.CAMERA}, PERMISSION_REQUEST_CODE);
                    }
                });
            }

            // Handle File Upload & Print Receipt Download
            @Override
            public boolean onShowFileChooser(WebView webView, ValueCallback<Uri[]> filePathCallback, FileChooserParams fileChooserParams) {
                fileUploadCallback = filePathCallback;
                Intent intent = fileChooserParams.createIntent();
                try {
                    startActivityForResult(intent, FILE_CHOOSER_REQUEST_CODE);
                } catch (Exception e) {
                    fileUploadCallback = null;
                    return false;
                }
                return true;
            }
        });
    }

    private void showErrorHtml(WebView view, String title, String message) {
        String html = "<!DOCTYPE html><html><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'>" +
            "<style>" +
            "body{margin:0;background:#0B0F19;color:#F8FAFC;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center;padding:1.5rem;box-sizing:border-box;}" +
            ".card{background:#111827;border:1px solid rgba(255,255,255,0.1);padding:2.2rem 1.5rem;border-radius:1.5rem;max-width:380px;width:100%;box-shadow:0 20px 40px rgba(0,0,0,0.5);}" +
            ".icon{width:64px;height:64px;border-radius:1.25rem;background:rgba(239,68,68,0.15);color:#EF4444;display:inline-flex;align-items:center;justify-content:center;font-size:2rem;margin-bottom:1.2rem;}" +
            "h2{margin:0 0 0.5rem;font-size:1.35rem;font-weight:800;letter-spacing:-0.02em;}" +
            "p{color:#94A3B8;font-size:0.9rem;line-height:1.55;margin:0 0 1.75rem;}" +
            ".btn{display:inline-block;width:100%;padding:14px 0;background:linear-gradient(135deg,#F97316,#0284C7);color:#fff;font-weight:700;font-size:1rem;border:none;border-radius:0.85rem;cursor:pointer;text-decoration:none;box-sizing:border-box;box-shadow:0 4px 15px rgba(2,132,199,0.35);}" +
            "</style></head><body><div class='card'>" +
            "<div class='icon'>⚡</div>" +
            "<h2>" + title + "</h2>" +
            "<p>" + message + "</p>" +
            "<button class='btn' onclick='location.reload()'>Повторить попытку</button>" +
            "</div></body></html>";
        view.loadDataWithBaseURL(null, html, "text/html", "UTF-8", null);
    }

    private void setupSwipeRefresh() {
        swipeRefreshLayout.setColorSchemeResources(R.color.primary, R.color.accent_orange);
        swipeRefreshLayout.setOnRefreshListener(() -> webView.reload());
        
        // Disable swipe refresh when scrolling inside WebView
        webView.getViewTreeObserver().addOnScrollChangedListener(() -> {
            swipeRefreshLayout.setEnabled(webView.getScrollY() == 0);
        });
    }

    private void checkAppPermissions() {
        java.util.List<String> permissionsNeeded = new java.util.ArrayList<>();
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            permissionsNeeded.add(Manifest.permission.CAMERA);
        }
        if (Build.VERSION.SDK_INT >= 33) { // Android 13+ POST_NOTIFICATIONS
            if (ContextCompat.checkSelfPermission(this, "android.permission.POST_NOTIFICATIONS") != PackageManager.PERMISSION_GRANTED) {
                permissionsNeeded.add("android.permission.POST_NOTIFICATIONS");
            }
        }
        if (!permissionsNeeded.isEmpty()) {
            ActivityCompat.requestPermissions(this, permissionsNeeded.toArray(new String[0]), PERMISSION_REQUEST_CODE);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions, @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERMISSION_REQUEST_CODE) {
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                runOnUiThread(() -> {
                    if (pendingPermissionRequest != null) {
                        try {
                            pendingPermissionRequest.grant(pendingPermissionRequest.getResources());
                        } catch (Exception e) {
                            try {
                                pendingPermissionRequest.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
                            } catch (Exception ignored) {}
                        }
                        pendingPermissionRequest = null;
                    }
                });
            } else {
                Toast.makeText(this, "Для сканирования требуется доступ к камере", Toast.LENGTH_SHORT).show();
            }
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, @Nullable Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == FILE_CHOOSER_REQUEST_CODE) {
            if (fileUploadCallback != null) {
                Uri[] results = null;
                if (resultCode == RESULT_OK && data != null) {
                    String dataString = data.getDataString();
                    if (dataString != null) {
                        results = new Uri[]{Uri.parse(dataString)};
                    }
                }
                fileUploadCallback.onReceiveValue(results);
                fileUploadCallback = null;
            }
        }
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
