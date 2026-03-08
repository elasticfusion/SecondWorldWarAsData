package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"

	"gopkg.in/yaml.v3"
)

type SearchResult struct {
	Rank        int    `json:"rank"`
	URL         string `json:"url"`
	Title       string `json:"title"`
	Description string `json:"description"`
	Engine      string `json:"engine"`
}

type BlacklistConfig struct {
	Blacklist            []string `yaml:"blacklist"`
	SourceMaterialPaths  []string `yaml:"source_material_paths"`
	Whitelist            []string `yaml:"whitelist"`
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
	place := flag.String("place", "", "Place name to search for")
	date := flag.String("date", "", "Date (YYYY-MM-DD)")
	limit := flag.Int("limit", 20, "Max results")
	openserp := flag.String("openserp", "http://localhost:7000", "OpenSERP URL")
	blacklistPath := flag.String("blacklist", "domain_blacklist.yaml", "Path to blacklist YAML")
	flag.Parse()

	if *place == "" {
		fmt.Fprintln(os.Stderr, "Error: -place required")
		os.Exit(1)
	}

	// Load blacklist
	blacklist, sourcePaths, whitelist, err := loadBlacklist(*blacklistPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Warning: Could not load blacklist from %s: %v\n", *blacklistPath, err)
		blacklist = []string{} // Continue with empty blacklist
		sourcePaths = []string{}
		whitelist = []string{}
	}

	// Build search query
	year := ""
	if *date != "" {
		year = strings.Split(*date, "-")[0]
	}
	query := fmt.Sprintf("WWII map \"%s\" %s", *place, year)

	// Search with OpenSERP
	results, err := searchOpenSERP(*openserp, query, *limit)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Search failed: %v\n", err)
		os.Exit(1)
	}

	// Filter for map-related URLs
	mapResults := filterMapURLs(results, *place, blacklist, sourcePaths, whitelist)

	// Output as JSON
	output, _ := json.MarshalIndent(mapResults, "", "  ")
	fmt.Println(string(output))
}

func searchOpenSERP(baseURL, query string, limit int) ([]SearchResult, error) {
	params := url.Values{}
	params.Set("text", query)
	params.Set("engines", "google,bing,duckduckgo")
	params.Set("limit", fmt.Sprintf("%d", limit))

	resp, err := http.Get(fmt.Sprintf("%s/mega/search?%s", baseURL, params.Encode()))
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	var results []SearchResult
	if err := json.Unmarshal(body, &results); err != nil {
		return nil, err
	}

	return results, nil
}

func filterMapURLs(results []SearchResult, place string, blacklist []string, sourcePaths []string, whitelist []string) []SearchResult {
	var filtered []SearchResult
	placeLower := strings.ToLower(place)

	for _, r := range results {
		titleLower := strings.ToLower(r.Title)
		descLower := strings.ToLower(r.Description)
		urlLower := strings.ToLower(r.URL)

		// Check whitelist first - if whitelisted, skip all other checks
		isWhitelisted := false
		for _, domain := range whitelist {
			if strings.Contains(urlLower, strings.ToLower(domain)) {
				isWhitelisted = true
				break
			}
		}

		if !isWhitelisted {
			// Check domain blacklist (blacklist is decisive)
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

			// Check source material paths (already in our repository)
			isSourceMaterial := false
			for _, path := range sourcePaths {
				if strings.Contains(urlLower, strings.ToLower(path)) {
					isSourceMaterial = true
					break
				}
			}
			if isSourceMaterial {
				continue
			}
		}

		// Check if it's map-related
		isMap := strings.Contains(titleLower, "map") ||
			strings.Contains(descLower, "map") ||
			strings.Contains(urlLower, "map")

		// Check if it mentions the place
		hasPlace := strings.Contains(titleLower, placeLower) ||
			strings.Contains(descLower, placeLower)

		if isMap && hasPlace {
			filtered = append(filtered, r)
		}
	}

	return filtered
}
