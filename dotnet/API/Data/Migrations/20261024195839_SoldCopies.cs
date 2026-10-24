using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace API.Data.Migrations
{
    /// <inheritdoc />
    public partial class SoldCopies : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<DateTime>(
                name: "SoldAt",
                table: "TrackedCards",
                type: "TEXT",
                nullable: true);

            migrationBuilder.AddColumn<double>(
                name: "SoldPrice",
                table: "TrackedCards",
                type: "REAL",
                nullable: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "SoldAt",
                table: "TrackedCards");

            migrationBuilder.DropColumn(
                name: "SoldPrice",
                table: "TrackedCards");
        }
    }
}
