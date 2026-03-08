package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"os"
	"strings"

	"gopkg.in/yaml.v3"
)

type MediaItem struct {
	MediaType   string `json:"media_type"`
	URL         string `json:"url"`
	Title       string `json:"title"`
	Source      string `json:"source"`
	License     string `json:"license,omitempty"`
	Description string `json:"description,omitempty"`
}

type SearchResult struct {
	Title       string `json:"title"`
	URL         string `json:"url"`
	Description string `json:"description"`
}

type BlacklistConfig struct {
	Blacklist           []string `yaml:"blacklist"`
	SourceMaterialPaths []string `yaml:"source_material_paths"`
	Whitelist           []string `yaml:"whitelist"`
}

func loadBlacklist(path string) ([]string, []string, []string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, nil, nil, err
	}

	var config BlacklistConfig
	if err := yaml.Unmarshal(data, &config); err != nil {
		return nil, nil, nil, err
	}

	return config.Blacklist, config.SourceMaterialPaths, config.Whitelist, nil
}

func main() {
	if len(os.Args) < 2 {
		log.Fatal("Usage: search_media <query>")
	}

	query := strings.Join(os.Args[1:], " ")

	// Load blacklist
	blacklist, _, whitelist, err := loadBlacklist("domain_blacklist.yaml")
	if err != nil {
		log.Printf("Warning: Could not load blacklist: %v", err)
		blacklist = []string{}
		whitelist = []string{}
	}

	// Try OpenSERP if available
	media, err := searchWithOpenSERP(query, blacklist, whitelist)
	if err != nil {
		log.Printf("OpenSERP search failed: %v", err)
		media = []MediaItem{}
	}

	// Output JSON
	output, _ := json.Marshal(media)
	fmt.Println(string(output))
}

func searchWithOpenSERP(query string, blacklist []string, whitelist []string) ([]MediaItem, error) {
	// Check if OpenSERP is running
	openSERPURL := os.Getenv("OPENSERP_URL")
	if openSERPURL == "" {
		openSERPURL = "http://localhost:7001"
	}

	// Search using mega/search endpoint (same as maps)
	params := url.Values{}
	params.Set("text", query)
	params.Set("engines", "google,bing,duckduckgo")
	params.Set("limit", "10")

	searchURL := fmt.Sprintf("%s/mega/search?%s", openSERPURL, params.Encode())

	resp, err := http.Get(searchURL)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("OpenSERP returned status %d", resp.StatusCode)
	}

	// Response is a direct array of results
	var results []struct {
		Title       string `json:"title"`
		URL         string `json:"url"`
		Description string `json:"description"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&results); err != nil {
		return nil, err
	}

	// Convert to MediaItem format
	media := []MediaItem{}
	for _, result := range results {
		urlLower := strings.ToLower(result.URL)

		// Check whitelist first
		isWhitelisted := false
		for _, domain := range whitelist {
			if strings.Contains(urlLower, strings.ToLower(domain)) {
				isWhitelisted = true
				break
			}
		}

		// Check blacklist if not whitelisted
		if !isWhitelisted {
			isBlacklisted := false
			for _, domain := range blacklist {
				if strings.Contains(urlLower, strings.ToLower(domain)) {
					isBlacklisted = true
					break
				}
			}
			if isBlacklisted {
				continue
			}
		}

		// Determine source from URL
		source := "unknown"
		if strings.Contains(result.URL, "wikipedia.org") {
			source = "wikipedia"
		} else if strings.Contains(result.URL, "wikimedia.org") {
			source = "commons"
		} else if strings.Contains(result.URL, "archive.org") {
			source = "archive"
		}

		// Only include Wikipedia/Commons/Archive sources
		if source == "unknown" {
			continue
		}

		media = append(media, MediaItem{
			MediaType:   "photo",
			URL:         result.URL,
			Title:       result.Title,
			Source:      source,
			License:     "See source",
			Description: result.Title,
		})
	}

	return media, nil
}
